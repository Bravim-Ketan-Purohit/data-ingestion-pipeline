"""Tests proving the server-side commit gate works.

The commit gate is SERVER-SIDE. A document with an unverified required field
or a validation error returns 409. A disabled button in the UI is NOT the gate.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.db.models import DocState, Document, Field, Schema
from pipeline.review.service import CommitGateError, ReviewService


@pytest.fixture
def mock_session():
    """Create a mock async session."""
    session = AsyncMock()
    return session


@pytest.fixture
def sample_schema_obj():
    """A sample schema with required fields."""
    return Schema(
        id=uuid.uuid4(),
        name="test_schema",
        version=1,
        json_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
            "additionalProperties": False,
        },
    )


@pytest.fixture
def sample_document(sample_schema_obj):
    """A document in review state."""
    return Document(
        id=uuid.uuid4(),
        filename="test.pdf",
        content_hash="abc123",
        mime="application/pdf",
        size_bytes=1000,
        s3_key="uploads/test.pdf",
        schema_id=sample_schema_obj.id,
        state=DocState.review,
        created_at=datetime.now(timezone.utc),
    )


class TestCommitGate:
    """Test that the commit gate is enforced server-side."""

    @pytest.mark.asyncio
    async def test_commit_blocked_unverified_required_field(
        self, mock_session, sample_document, sample_schema_obj
    ):
        """A document with unverified required fields returns 409."""
        # Setup: field exists but is not verified
        field_name = Field(
            id=uuid.uuid4(),
            document_id=sample_document.id,
            path="/name",
            value="Alice",
            confidence=0.95,
            verified=False,  # NOT verified
        )
        field_age = Field(
            id=uuid.uuid4(),
            document_id=sample_document.id,
            path="/age",
            value=30,
            confidence=0.9,
            verified=True,
        )

        mock_session.get = AsyncMock(side_effect=lambda cls, id: {
            Document: sample_document,
            Schema: sample_schema_obj,
        }.get(cls))

        # Mock the fields query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [field_name, field_age]
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = ReviewService(mock_session)

        with pytest.raises(CommitGateError) as exc_info:
            await service.commit_document(sample_document.id)

        errors = exc_info.value.errors
        assert any(e["type"] == "unverified_required" for e in errors)
        assert any("/name" in e.get("path", "") for e in errors)

    @pytest.mark.asyncio
    async def test_commit_blocked_validation_error(
        self, mock_session, sample_document, sample_schema_obj
    ):
        """A document with validation errors returns 409."""
        field_name = Field(
            id=uuid.uuid4(),
            document_id=sample_document.id,
            path="/name",
            value="Alice",
            confidence=0.95,
            verified=True,
            validation_error="some error",  # Has a validation error
        )
        field_age = Field(
            id=uuid.uuid4(),
            document_id=sample_document.id,
            path="/age",
            value=30,
            confidence=0.9,
            verified=True,
        )

        mock_session.get = AsyncMock(side_effect=lambda cls, id: {
            Document: sample_document,
            Schema: sample_schema_obj,
        }.get(cls))

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [field_name, field_age]
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = ReviewService(mock_session)

        with pytest.raises(CommitGateError) as exc_info:
            await service.commit_document(sample_document.id)

        errors = exc_info.value.errors
        assert any(e["type"] == "validation_error" for e in errors)

    @pytest.mark.asyncio
    async def test_commit_blocked_wrong_state(self, mock_session, sample_schema_obj):
        """A document not in 'review' state cannot be committed."""
        doc = Document(
            id=uuid.uuid4(),
            filename="test.pdf",
            content_hash="abc123",
            mime="application/pdf",
            size_bytes=1000,
            s3_key="uploads/test.pdf",
            schema_id=sample_schema_obj.id,
            state=DocState.extracting,  # Wrong state
            created_at=datetime.now(timezone.utc),
        )

        mock_session.get = AsyncMock(return_value=doc)

        service = ReviewService(mock_session)

        with pytest.raises(CommitGateError) as exc_info:
            await service.commit_document(doc.id)

        errors = exc_info.value.errors
        assert any(e["type"] == "invalid_state" for e in errors)
