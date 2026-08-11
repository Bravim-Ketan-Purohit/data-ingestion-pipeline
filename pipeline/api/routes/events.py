"""SSE events endpoint for real-time progress updates."""

import asyncio
import json
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.engine import get_session
from pipeline.db.models import Document

router = APIRouter()


async def event_stream(document_id: uuid.UUID, session: AsyncSession) -> AsyncGenerator[str, None]:
    """Generate SSE events for document processing progress."""
    while True:
        doc = await session.get(Document, document_id)
        if doc is None:
            yield f"data: {json.dumps({'error': 'Document not found'})}\n\n"
            break

        yield f"data: {json.dumps({'state': doc.state.value, 'document_id': str(doc.id)})}\n\n"

        # Stop streaming when document reaches a terminal state
        if doc.state.value in ("committed", "failed", "review"):
            break

        await asyncio.sleep(1)
        await session.refresh(doc)


@router.get("/{document_id}")
async def get_events(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    """SSE progress for the upload/extract pipeline."""
    return StreamingResponse(
        event_stream(document_id, session),
        media_type="text/event-stream",
    )
