"""Upload routes: presigned multipart S3 uploads.

HARD RULE: File bytes NEVER pass through the API.
The API issues presigned part URLs and the browser uploads directly to S3.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.engine import get_session
from pipeline.uploads.service import UploadService

router = APIRouter()


class InitiateUploadRequest(BaseModel):
    """Request to initiate a multipart upload."""

    filename: str
    size: int = Field(gt=0)
    mime: str
    schema_id: uuid.UUID
    content_hash: str = Field(description="SHA-256 hash of file content for dedupe")


class PartInfo(BaseModel):
    """Info about a single upload part."""

    part_number: int
    etag: str


class CompleteUploadRequest(BaseModel):
    """Request to complete a multipart upload."""

    parts: list[PartInfo]


@router.post("")
async def initiate_upload(
    request: InitiateUploadRequest,
    session: AsyncSession = Depends(get_session),
):
    """Initiate a presigned multipart upload.

    Returns document_id, upload_id, and presigned URLs for each part.
    If content_hash matches an existing document, returns that (dedupe).
    """
    service = UploadService(session)
    try:
        result = await service.initiate_upload(
            filename=request.filename,
            size_bytes=request.size,
            mime=request.mime,
            schema_id=request.schema_id,
            content_hash=request.content_hash,
        )
        await session.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{document_id}/parts")
async def get_upload_parts(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get which parts S3 already has (resume support).

    On resume after a page refresh, the client calls this to find out which
    parts are already uploaded and which still need to be sent.
    """
    service = UploadService(session)
    try:
        result = await service.get_uploaded_parts(document_id)
        await session.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{document_id}/complete")
async def complete_upload(
    document_id: uuid.UUID,
    request: CompleteUploadRequest,
    session: AsyncSession = Depends(get_session),
):
    """Complete the multipart upload. Triggers partition + extract pipeline."""
    service = UploadService(session)
    try:
        parts = [{"part_number": p.part_number, "etag": p.etag} for p in request.parts]
        doc = await service.complete_upload(document_id, parts)
        await session.commit()
        return {"document_id": str(doc.id), "state": doc.state.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{document_id}/abort")
async def abort_upload(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Abort a multipart upload. Cleans up S3 parts."""
    service = UploadService(session)
    try:
        await service.abort_upload(document_id)
        await session.commit()
        return {"status": "aborted"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
