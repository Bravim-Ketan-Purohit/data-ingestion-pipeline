"""Export service: validated JSON / NDJSON per document."""

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.models import DocState, Document, Field
from pipeline.observability.logging import get_logger

logger = get_logger(__name__)


class ExportService:
    """Exports committed documents as validated JSON or NDJSON."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def export_document(self, document_id: uuid.UUID, format: str = "json") -> str:
        """Export a single committed document.

        Args:
            document_id: The document to export
            format: 'json' or 'ndjson'

        Returns:
            JSON string of the validated document

        Raises:
            ValueError if document is not committed
        """
        doc = await self._session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found")

        if doc.state != DocState.committed:
            raise ValueError(
                f"Document {document_id} is not committed (state: {doc.state.value}). "
                "Only committed documents can be exported."
            )

        fields_result = await self._session.execute(
            select(Field).where(Field.document_id == document_id)
        )
        fields = fields_result.scalars().all()

        # Build the output document
        output = self._build_output(doc, fields)

        if format == "ndjson":
            return json.dumps(output, default=str)
        else:
            return json.dumps(output, indent=2, default=str)

    async def export_batch(self, document_ids: list[uuid.UUID] | None = None) -> str:
        """Export multiple documents as NDJSON (one JSON object per line).

        If document_ids is None, exports all committed documents.
        """
        if document_ids is None:
            result = await self._session.execute(
                select(Document).where(Document.state == DocState.committed)
            )
            docs = result.scalars().all()
        else:
            result = await self._session.execute(
                select(Document).where(
                    Document.id.in_(document_ids),
                    Document.state == DocState.committed,
                )
            )
            docs = result.scalars().all()

        lines = []
        for doc in docs:
            fields_result = await self._session.execute(
                select(Field).where(Field.document_id == doc.id)
            )
            fields = fields_result.scalars().all()
            output = self._build_output(doc, fields)
            lines.append(json.dumps(output, default=str))

        return "\n".join(lines)

    def _build_output(self, doc: Document, fields: list[Field]) -> dict[str, Any]:
        """Build the output document from fields."""
        data: dict[str, Any] = {}

        for field in fields:
            # Convert JSON pointer path to nested dict
            parts = [p for p in field.path.strip("/").split("/") if p]
            current = data
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            if parts:
                current[parts[-1]] = field.value

        return {
            "_metadata": {
                "document_id": str(doc.id),
                "filename": doc.filename,
                "schema_id": str(doc.schema_id),
                "committed_at": doc.committed_at.isoformat() if doc.committed_at else None,
                "content_hash": doc.content_hash,
            },
            "data": data,
        }
