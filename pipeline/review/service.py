"""Review service: verification state machine, corrections, commit gate.

The commit gate is SERVER-SIDE. An unverified required field or a validation error
returns 409. A disabled button in the UI is NOT the gate.

A document cannot be committed while any required field is unverified
or any validation error stands.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.db.models import DocState, Document, Field, Schema
from pipeline.observability.logging import get_logger
from pipeline.observability.tracing import SPAN_COMMIT, SPAN_VALIDATE, get_tracer
from pipeline.schemas.registry import SchemaValidator

logger = get_logger(__name__)
tracer = get_tracer(__name__)


class CommitGateError(Exception):
    """Raised when a document cannot be committed due to unverified/invalid fields."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"Commit blocked: {len(errors)} issue(s)")


class ReviewService:
    """Manages the review workflow: verify fields, correct values, enforce commit gate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_document_fields(self, document_id: uuid.UUID) -> list[Field]:
        """Get all fields for a document, sorted by confidence (lowest first for review)."""
        result = await self._session.execute(
            select(Field)
            .where(Field.document_id == document_id)
            .order_by(Field.confidence.asc().nullsfirst())
        )
        return list(result.scalars().all())

    async def correct_field(
        self,
        document_id: uuid.UUID,
        field_path: str,
        new_value: Any,
        corrected_by: str = "operator",
    ) -> Field:
        """Correct a field value. Records the original for audit trail.

        The correction log is both an audit trail and the beginnings of an eval set —
        it tells you which fields the extractor is actually bad at.
        """
        result = await self._session.execute(
            select(Field).where(
                Field.document_id == document_id,
                Field.path == field_path,
            )
        )
        field = result.scalar_one_or_none()
        if field is None:
            raise ValueError(f"Field '{field_path}' not found for document {document_id}")

        # Record the correction
        field.corrected_from = field.value
        field.value = new_value
        field.corrected_by = corrected_by
        field.corrected_at = datetime.now(timezone.utc)

        # Re-validate the new value against the schema
        doc = await self._session.get(Document, document_id)
        schema = await self._session.get(Schema, doc.schema_id)
        validator = SchemaValidator(schema.json_schema)
        error = validator.validate_field(field_path, new_value)
        field.validation_error = error

        await self._session.flush()

        logger.info(
            "field_corrected",
            document_id=str(document_id),
            field_path=field_path,
            corrected_by=corrected_by,
        )
        return field

    async def verify_fields(
        self, document_id: uuid.UUID, paths: list[str]
    ) -> list[Field]:
        """Mark fields as verified by the operator."""
        result = await self._session.execute(
            select(Field).where(
                Field.document_id == document_id,
                Field.path.in_(paths),
            )
        )
        fields = result.scalars().all()

        for field in fields:
            field.verified = True

        await self._session.flush()

        logger.info(
            "fields_verified",
            document_id=str(document_id),
            paths=paths,
            count=len(fields),
        )
        return list(fields)

    async def commit_document(self, document_id: uuid.UUID) -> Document:
        """Commit a document. Enforces the server-side commit gate.

        Returns 409 (via CommitGateError) if:
        - Any required field is unverified
        - Any validation error stands
        - Document is not in 'review' state
        """
        with tracer.start_as_current_span(
            SPAN_COMMIT, attributes={"document_id": str(document_id)}
        ):
            doc = await self._session.get(Document, document_id)
            if doc is None:
                raise ValueError(f"Document {document_id} not found")

            if doc.state != DocState.review:
                raise CommitGateError([{
                    "type": "invalid_state",
                    "message": f"Document is in state '{doc.state.value}', expected 'review'",
                }])

            # Get the schema to find required fields
            schema = await self._session.get(Schema, doc.schema_id)
            required_paths = set(
                f"/{p}" for p in schema.json_schema.get("required", [])
            )

            # Get all fields
            fields_result = await self._session.execute(
                select(Field).where(Field.document_id == document_id)
            )
            fields = fields_result.scalars().all()

            # Check the commit gate
            errors = []

            # Check for unverified required fields
            field_map = {f.path: f for f in fields}
            for req_path in required_paths:
                if req_path not in field_map:
                    errors.append({
                        "type": "missing_required",
                        "path": req_path,
                        "message": f"Required field '{req_path}' not extracted",
                    })
                elif not field_map[req_path].verified:
                    errors.append({
                        "type": "unverified_required",
                        "path": req_path,
                        "message": f"Required field '{req_path}' has not been verified",
                    })

            # Check for validation errors
            for field in fields:
                if field.validation_error:
                    errors.append({
                        "type": "validation_error",
                        "path": field.path,
                        "message": field.validation_error,
                    })

            # Validate the complete document against the schema
            with tracer.start_as_current_span(SPAN_VALIDATE):
                doc_data = {}
                for field in fields:
                    # Convert JSON pointer path to nested dict
                    parts = [p for p in field.path.strip("/").split("/") if p]
                    current = doc_data
                    for part in parts[:-1]:
                        if part not in current:
                            current[part] = {}
                        current = current[part]
                    if parts:
                        current[parts[-1]] = field.value

                validator = SchemaValidator(schema.json_schema)
                schema_errors = validator.validate(doc_data)
                for err in schema_errors:
                    errors.append({
                        "type": "schema_validation",
                        "path": err["path"],
                        "message": err["message"],
                    })

            if errors:
                raise CommitGateError(errors)

            # All checks pass — commit
            doc.state = DocState.committed
            doc.committed_at = datetime.now(timezone.utc)
            await self._session.flush()

            logger.info("document_committed", document_id=str(document_id))
            return doc
