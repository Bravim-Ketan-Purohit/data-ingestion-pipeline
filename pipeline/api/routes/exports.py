"""Export routes: JSON / NDJSON export of committed documents."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.engine import get_session
from pipeline.export.service import ExportService

router = APIRouter()


@router.get("/documents/{document_id}/export")
async def export_document(
    document_id: uuid.UUID,
    format: str = "json",
    session: AsyncSession = Depends(get_session),
):
    """Export a committed document as JSON or NDJSON."""
    if format not in ("json", "ndjson"):
        raise HTTPException(status_code=400, detail="format must be 'json' or 'ndjson'")

    service = ExportService(session)
    try:
        content = await service.export_document(document_id, format)
        content_type = "application/json" if format == "json" else "application/x-ndjson"
        return Response(content=content, media_type=content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/export/batch")
async def export_batch(
    session: AsyncSession = Depends(get_session),
):
    """Export all committed documents as NDJSON."""
    service = ExportService(session)
    content = await service.export_batch()
    return Response(content=content, media_type="application/x-ndjson")
