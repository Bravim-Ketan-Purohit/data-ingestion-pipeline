"""Timing routes: measurement protocol endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.engine import get_session
from pipeline.timing.service import TimingService

router = APIRouter()


class StartTimingRequest(BaseModel):
    """Request to start a timing session."""

    document_id: uuid.UUID
    participant: str
    arm: str  # "manual" or "tool"


class FinishTimingRequest(BaseModel):
    """Request to finish a timing session."""

    active_seconds: int
    fields_corrected: int
    accuracy: float


@router.post("/start")
async def start_timing(
    request: StartTimingRequest,
    session: AsyncSession = Depends(get_session),
):
    """Start a timing session for the measurement protocol."""
    service = TimingService(session)
    try:
        timing = await service.start_timing(
            document_id=request.document_id,
            participant=request.participant,
            arm=request.arm,
        )
        await session.commit()
        return {
            "timing_id": str(timing.id),
            "started_at": timing.started_at.isoformat(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{timing_id}/finish")
async def finish_timing(
    timing_id: uuid.UUID,
    request: FinishTimingRequest,
    session: AsyncSession = Depends(get_session),
):
    """Complete a timing session with results."""
    service = TimingService(session)
    try:
        timing = await service.finish_timing(
            timing_id=timing_id,
            active_seconds=request.active_seconds,
            fields_corrected=request.fields_corrected,
            accuracy=request.accuracy,
        )
        await session.commit()
        return {
            "timing_id": str(timing.id),
            "active_seconds": timing.active_seconds,
            "accuracy": timing.accuracy,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/comparison")
async def get_comparison(
    session: AsyncSession = Depends(get_session),
):
    """Get the manual vs tool comparison for the Benchmarks table."""
    service = TimingService(session)
    return await service.get_comparison()
