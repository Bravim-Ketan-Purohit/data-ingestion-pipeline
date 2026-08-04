"""Initial schema: documents, upload_parts, partitions, fields, timings, schemas.

Revision ID: 001
Revises: None
Create Date: 2026-08-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum type
    op.execute("CREATE TYPE doc_state AS ENUM ('uploading', 'uploaded', 'partitioning', 'extracting', 'review', 'committed', 'failed')")

    # Schemas table
    op.create_table(
        "schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("json_schema", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", "version", name="uq_schema_name_version"),
    )

    # Documents table
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("schema_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="uploading"),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_documents_state", "documents", ["state"])

    # Upload parts table
    op.create_table(
        "upload_parts",
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("part_number", sa.Integer(), primary_key=True),
        sa.Column("upload_id", sa.Text(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Partitions table
    op.create_table(
        "partitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("row_range", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_partitions_document", "partitions", ["document_id"])

    # Fields table
    op.create_table(
        "fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_partition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("partitions.id"), nullable=True),
        sa.Column("source_span", postgresql.JSONB(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("corrected_from", postgresql.JSONB(), nullable=True),
        sa.Column("corrected_by", sa.Text(), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("document_id", "path", name="uq_field_document_path"),
    )
    op.create_index("ix_fields_document", "fields", ["document_id"])

    # Timings table (measurement harness — built first per causal order)
    op.create_table(
        "timings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("participant", sa.Text(), nullable=False),
        sa.Column("arm", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_seconds", sa.Integer(), nullable=True),
        sa.Column("fields_corrected", sa.Integer(), nullable=True),
        sa.Column("accuracy", sa.Float(), nullable=True),
    )
    op.create_index("ix_timings_document", "timings", ["document_id"])


def downgrade() -> None:
    op.drop_table("timings")
    op.drop_table("fields")
    op.drop_table("partitions")
    op.drop_table("upload_parts")
    op.drop_table("documents")
    op.drop_table("schemas")
    op.execute("DROP TYPE IF EXISTS doc_state")
