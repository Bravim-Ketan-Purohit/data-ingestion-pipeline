"""Document routes: state, partitions, fields, corrections, verify, commit."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.engine import get_session
from pipeline.db.models import DocState, Document, Field, Partition
from pipeline.review.service import CommitGateError, ReviewService
from pipeline.uploads.s3 import S3MultipartManager

router = APIRouter()


class CorrectFieldRequest(BaseModel):
    """Request to correct a field value."""

    value: Any


class VerifyFieldsRequest(BaseModel):
    """Request to mark fields as verified."""

    paths: list[str]


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get document with state, partitions, and fields (with provenance + confidence)."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Get partitions
    partitions_result = await session.execute(
        select(Partition)
        .where(Partition.document_id == document_id)
        .order_by(Partition.ordinal)
    )
    partitions = partitions_result.scalars().all()

    # Get fields (sorted by confidence ascending — low confidence first for review)
    fields_result = await session.execute(
        select(Field)
        .where(Field.document_id == document_id)
        .order_by(Field.confidence.asc().nullsfirst())
    )
    fields = fields_result.scalars().all()

    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "mime": doc.mime,
        "size_bytes": doc.size_bytes,
        "state": doc.state.value,
        "schema_id": str(doc.schema_id),
        "cost_usd": float(doc.cost_usd),
        "created_at": doc.created_at.isoformat(),
        "committed_at": doc.committed_at.isoformat() if doc.committed_at else None,
        "partitions": [
            {
                "id": str(p.id),
                "ordinal": p.ordinal,
                "kind": p.kind,
                "page": p.page,
                "bbox": p.bbox,
                "row_range": p.row_range,
                "content_length": len(p.content),
            }
            for p in partitions
        ],
        "fields": [
            {
                "id": str(f.id),
                "path": f.path,
                "value": f.value,
                "confidence": f.confidence,
                "source_partition_id": str(f.source_partition_id) if f.source_partition_id else None,
                "source_span": f.source_span,
                "verified": f.verified,
                "corrected_from": f.corrected_from,
                "corrected_by": f.corrected_by,
                "corrected_at": f.corrected_at.isoformat() if f.corrected_at else None,
                "validation_error": f.validation_error,
            }
            for f in fields
        ],
    }


@router.get("/{document_id}/source")
async def get_document_source(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Get a presigned GET URL for viewing the source document."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    s3 = S3MultipartManager()
    url = s3.generate_presigned_get_url(doc.s3_key)
    return {"url": url, "mime": doc.mime, "filename": doc.filename}


@router.patch("/{document_id}/fields/{field_path:path}")
async def correct_field(
    document_id: uuid.UUID,
    field_path: str,
    request: CorrectFieldRequest,
    session: AsyncSession = Depends(get_session),
):
    """Correct a field value. Records the original for audit trail."""
    service = ReviewService(session)
    try:
        # Ensure path starts with /
        path = f"/{field_path}" if not field_path.startswith("/") else field_path
        field = await service.correct_field(document_id, path, request.value)
        await session.commit()
        return {
            "id": str(field.id),
            "path": field.path,
            "value": field.value,
            "corrected_from": field.corrected_from,
            "validation_error": field.validation_error,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{document_id}/verify")
async def verify_fields(
    document_id: uuid.UUID,
    request: VerifyFieldsRequest,
    session: AsyncSession = Depends(get_session),
):
    """Mark fields as verified by the operator."""
    service = ReviewService(session)
    fields = await service.verify_fields(document_id, request.paths)
    await session.commit()
    return {"verified": [f.path for f in fields]}


@router.post("/{document_id}/commit")
async def commit_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """Commit a document. Returns 409 if any required field is unverified or invalid.

    The commit gate is SERVER-SIDE. This is not a UI convenience —
    it's the enforcement mechanism.
    """
    service = ReviewService(session)
    try:
        doc = await service.commit_document(document_id)
        await session.commit()
        return {"document_id": str(doc.id), "state": doc.state.value, "committed_at": doc.committed_at.isoformat()}
    except CommitGateError as e:
        raise HTTPException(status_code=409, detail={"errors": e.errors})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
