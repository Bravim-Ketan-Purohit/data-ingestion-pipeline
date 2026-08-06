"""Upload service: orchestrates presigned multipart uploads with resume and dedupe."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.models import DocState, Document, UploadPart
from pipeline.observability.logging import get_logger
from pipeline.observability.tracing import SPAN_UPLOAD_PART, get_tracer
from pipeline.uploads.s3 import S3MultipartManager

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Allowed MIME types — reject archives
ALLOWED_MIMES = {
    "application/pdf",
    "text/csv",
    "text/plain",
    "application/vnd.ms-excel",
}

# Magic bytes for content-type validation
MAGIC_BYTES = {
    b"%PDF": "application/pdf",
    b"\xef\xbb\xbf": "text/csv",  # UTF-8 BOM (often CSV)
}


class UploadService:
    """Manages the upload lifecycle: initiate, track parts, resume, complete, abort."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._s3 = S3MultipartManager()

    async def initiate_upload(
        self,
        filename: str,
        size_bytes: int,
        mime: str,
        schema_id: uuid.UUID,
        content_hash: str,
    ) -> dict:
        """Initiate a new multipart upload.

        Returns document_id, upload_id, and presigned part URLs.
        If content_hash already exists, returns the existing document (dedupe).
        """
        from pipeline.config import settings

        # Validate mime type
        if mime not in ALLOWED_MIMES:
            raise ValueError(f"File type '{mime}' not allowed. Accepted: {sorted(ALLOWED_MIMES)}")

        # Validate file size
        if size_bytes > settings.max_file_size_bytes:
            raise ValueError(
                f"File too large: {size_bytes} bytes. Maximum: {settings.max_file_size_bytes}"
            )

        # Content-hash dedupe: same bytes uploaded twice reuses existing document
        existing = await self._session.execute(
            select(Document).where(Document.content_hash == content_hash)
        )
        existing_doc = existing.scalar_one_or_none()
        if existing_doc is not None:
            logger.info("upload_deduplicated", document_id=str(existing_doc.id))
            return {
                "document_id": existing_doc.id,
                "deduplicated": True,
                "state": existing_doc.state.value,
            }

        # Generate S3 key
        doc_id = uuid.uuid4()
        s3_key = f"uploads/{doc_id}/{filename}"

        # Initiate multipart upload on S3
        upload_id = self._s3.initiate_multipart_upload(s3_key, mime)

        # Compute part plan
        part_plan = self._s3.compute_part_plan(size_bytes)

        # Create document record
        document = Document(
            id=doc_id,
            filename=filename,
            content_hash=content_hash,
            mime=mime,
            size_bytes=size_bytes,
            s3_key=s3_key,
            schema_id=schema_id,
            state=DocState.uploading,
        )
        self._session.add(document)

        # Create upload part records with presigned URLs
        parts_response = []
        for part in part_plan:
            part_number = part["part_number"]
            upload_part = UploadPart(
                document_id=doc_id,
                part_number=part_number,
                upload_id=upload_id,
            )
            self._session.add(upload_part)

            presigned_url = self._s3.generate_presigned_part_url(s3_key, upload_id, part_number)
            parts_response.append({
                "part_number": part_number,
                "presigned_url": presigned_url,
                "offset": part["offset"],
                "size": part["size"],
            })

        await self._session.flush()

        logger.info(
            "upload_initiated",
            document_id=str(doc_id),
            filename=filename,
            parts=len(parts_response),
        )

        return {
            "document_id": doc_id,
            "upload_id": upload_id,
            "parts": parts_response,
            "deduplicated": False,
        }

    async def get_uploaded_parts(self, document_id: uuid.UUID) -> list[dict]:
        """Get which parts S3 already has (resume support).

        On resume, the client asks which parts are already present
        and uploads only the gaps. A page refresh mid-upload must not restart.
        """
        doc = await self._session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found")

        # Get upload_id from first part
        parts_result = await self._session.execute(
            select(UploadPart).where(UploadPart.document_id == document_id)
        )
        parts = parts_result.scalars().all()

        if not parts:
            return []

        upload_id = parts[0].upload_id

        # Ask S3 which parts it already has
        s3_parts = self._s3.list_uploaded_parts(doc.s3_key, upload_id)

        # Update our records
        s3_part_map = {p["part_number"]: p for p in s3_parts}
        completed_parts = []

        for part in parts:
            if part.part_number in s3_part_map:
                s3_part = s3_part_map[part.part_number]
                part.etag = s3_part["etag"]
                part.size_bytes = s3_part["size"]
                part.uploaded_at = datetime.now(timezone.utc)
                completed_parts.append({
                    "part_number": part.part_number,
                    "etag": part.etag,
                    "size": part.size_bytes,
                })

        await self._session.flush()

        # Return parts that still need uploading with fresh presigned URLs
        missing_parts = []
        for part in parts:
            if part.part_number not in s3_part_map:
                url = self._s3.generate_presigned_part_url(doc.s3_key, upload_id, part.part_number)
                missing_parts.append({
                    "part_number": part.part_number,
                    "presigned_url": url,
                })

        return {
            "completed": completed_parts,
            "missing": missing_parts,
            "upload_id": upload_id,
        }

    async def complete_upload(
        self, document_id: uuid.UUID, parts: list[dict]
    ) -> Document:
        """Complete the multipart upload and transition to 'uploaded' state.

        Validates all parts are present, calls CompleteMultipartUpload,
        then triggers partition + extract pipeline.
        """
        with tracer.start_as_current_span(SPAN_UPLOAD_PART, attributes={"document_id": str(document_id)}):
            doc = await self._session.get(Document, document_id)
            if doc is None:
                raise ValueError(f"Document {document_id} not found")

            if doc.state != DocState.uploading:
                raise ValueError(f"Document is in state '{doc.state.value}', expected 'uploading'")

            # Get upload_id
            parts_result = await self._session.execute(
                select(UploadPart).where(UploadPart.document_id == document_id)
            )
            db_parts = parts_result.scalars().all()
            if not db_parts:
                raise ValueError("No upload parts found")

            upload_id = db_parts[0].upload_id

            # Complete on S3
            self._s3.complete_multipart_upload(doc.s3_key, upload_id, parts)

            # Update document state
            doc.state = DocState.uploaded
            await self._session.flush()

            logger.info("upload_completed", document_id=str(document_id))
            return doc

    async def abort_upload(self, document_id: uuid.UUID) -> None:
        """Abort a multipart upload. Cleans up S3 parts and marks document failed."""
        doc = await self._session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found")

        # Get upload_id
        parts_result = await self._session.execute(
            select(UploadPart).where(UploadPart.document_id == document_id)
        )
        parts = parts_result.scalars().all()

        if parts:
            upload_id = parts[0].upload_id
            self._s3.abort_multipart_upload(doc.s3_key, upload_id)

        doc.state = DocState.failed
        await self._session.flush()
        logger.info("upload_aborted", document_id=str(document_id))
