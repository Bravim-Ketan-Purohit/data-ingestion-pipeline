"""Timing harness for the measurement protocol (SPEC §9).

Records manual vs tool arm timings, computes accuracy against ground truth,
and provides the data needed to fill [XX]% in the resume.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.models import Document, Field, Timing


class TimingService:
    """Manages timing records for the counterbalanced measurement protocol."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_timing(
        self,
        document_id: uuid.UUID,
        participant: str,
        arm: str,  # "manual" or "tool"
    ) -> Timing:
        """Start timing for a participant on a document in a given arm."""
        if arm not in ("manual", "tool"):
            raise ValueError(f"arm must be 'manual' or 'tool', got '{arm}'")

        timing = Timing(
            id=uuid.uuid4(),
            document_id=document_id,
            participant=participant,
            arm=arm,
            started_at=datetime.now(timezone.utc),
        )
        self._session.add(timing)
        await self._session.flush()
        return timing

    async def finish_timing(
        self,
        timing_id: uuid.UUID,
        active_seconds: int,
        fields_corrected: int,
        accuracy: float,
    ) -> Timing:
        """Complete a timing record with results."""
        timing = await self._session.get(Timing, timing_id)
        if timing is None:
            raise ValueError(f"Timing {timing_id} not found")

        timing.finished_at = datetime.now(timezone.utc)
        timing.active_seconds = active_seconds
        timing.fields_corrected = fields_corrected
        timing.accuracy = accuracy
        await self._session.flush()
        return timing

    async def auto_record_tool_timing(
        self,
        document_id: uuid.UUID,
        participant: str,
        started_at: datetime,
    ) -> Timing:
        """Auto-record tool arm timing from pipeline instrumentation.

        Called when a document reaches 'committed' state. Computes accuracy
        from the field corrections made during review.
        """
        # Count total fields and corrections
        fields_result = await self._session.execute(
            select(Field).where(Field.document_id == document_id)
        )
        fields = fields_result.scalars().all()
        total_fields = len(fields)
        corrected = sum(1 for f in fields if f.corrected_from is not None)
        accuracy = (total_fields - corrected) / total_fields if total_fields > 0 else 1.0

        now = datetime.now(timezone.utc)
        active_seconds = int((now - started_at).total_seconds())

        timing = Timing(
            id=uuid.uuid4(),
            document_id=document_id,
            participant=participant,
            arm="tool",
            started_at=started_at,
            finished_at=now,
            active_seconds=active_seconds,
            fields_corrected=corrected,
            accuracy=accuracy,
        )
        self._session.add(timing)
        await self._session.flush()
        return timing

    async def get_comparison(self) -> dict:
        """Compute manual vs tool comparison statistics.

        Returns the data needed for the Benchmarks table:
        - Median and total time per arm
        - Per-document distribution
        - Field-level accuracy per arm
        - Relative reduction percentage
        """
        result = await self._session.execute(select(Timing).where(Timing.finished_at.isnot(None)))
        timings = result.scalars().all()

        manual_times = sorted(
            [t.active_seconds for t in timings if t.arm == "manual" and t.active_seconds]
        )
        tool_times = sorted(
            [t.active_seconds for t in timings if t.arm == "tool" and t.active_seconds]
        )

        manual_accuracy = [t.accuracy for t in timings if t.arm == "manual" and t.accuracy is not None]
        tool_accuracy = [t.accuracy for t in timings if t.arm == "tool" and t.accuracy is not None]

        def median(values: list[int | float]) -> float:
            if not values:
                return 0.0
            n = len(values)
            mid = n // 2
            if n % 2 == 0:
                return (values[mid - 1] + values[mid]) / 2.0
            return float(values[mid])

        manual_median = median(manual_times)
        tool_median = median(tool_times)
        reduction_pct = (
            ((manual_median - tool_median) / manual_median * 100) if manual_median > 0 else 0.0
        )

        participants = set(t.participant for t in timings)

        return {
            "manual": {
                "count": len(manual_times),
                "median_seconds": manual_median,
                "total_seconds": sum(manual_times),
                "mean_accuracy": sum(manual_accuracy) / len(manual_accuracy) if manual_accuracy else None,
            },
            "tool": {
                "count": len(tool_times),
                "median_seconds": tool_median,
                "total_seconds": sum(tool_times),
                "mean_accuracy": sum(tool_accuracy) / len(tool_accuracy) if tool_accuracy else None,
            },
            "reduction_percent": round(reduction_pct, 1),
            "participant_count": len(participants),
            "total_documents": len(manual_times) + len(tool_times),
        }
