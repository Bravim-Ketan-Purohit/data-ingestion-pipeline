"""SQLAlchemy ORM models matching SPEC.md §8 data model."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class DocState(str, enum.Enum):
    """Document lifecycle states."""

    uploading = "uploading"
    uploaded = "uploaded"
    partitioning = "partitioning"
    extracting = "extracting"
    review = "review"
    committed = "committed"
    failed = "failed"


class Schema(Base):
    """User-supplied target JSON Schema."""

    __tablename__ = "schemas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    json_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    documents: Mapped[list["Document"]] = relationship(back_populates="schema")

    __table_args__ = (UniqueConstraint("name", "version", name="uq_schema_name_version"),)


class Document(Base):
    """A source document being processed through the pipeline."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schemas.id"), nullable=False
    )
    state: Mapped[DocState] = mapped_column(
        Enum(DocState, name="doc_state"), nullable=False, default=DocState.uploading
    )
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    schema: Mapped[Schema] = relationship(back_populates="documents")
    upload_parts: Mapped[list["UploadPart"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    partitions: Mapped[list["Partition"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    fields: Mapped[list["Field"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    timings: Mapped[list["Timing"]] = relationship(back_populates="document")

    __table_args__ = (Index("ix_documents_state", "state"),)


class UploadPart(Base):
    """Tracks individual parts of a multipart S3 upload for resume support."""

    __tablename__ = "upload_parts"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    part_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[str] = mapped_column(Text, nullable=False)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[Document] = relationship(back_populates="upload_parts")


class Partition(Base):
    """A typed element extracted from a document (page, heading, paragraph, table, kv_pair)."""

    __tablename__ = "partitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    row_range: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. "[1,50)"
    content: Mapped[str] = mapped_column(Text, nullable=False)

    document: Mapped[Document] = relationship(back_populates="partitions")
    fields: Mapped[list["Field"]] = relationship(back_populates="source_partition")

    __table_args__ = (Index("ix_partitions_document", "document_id"),)


class Field(Base):
    """An extracted field with provenance, confidence, and verification state."""

    __tablename__ = "fields"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)  # JSON pointer into target schema
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_partition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("partitions.id"), nullable=True
    )
    source_span: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # bbox or row/col
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    corrected_from: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    corrected_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="fields")
    source_partition: Mapped[Partition | None] = relationship(back_populates="fields")

    __table_args__ = (
        UniqueConstraint("document_id", "path", name="uq_field_document_path"),
        Index("ix_fields_document", "document_id"),
    )


class Timing(Base):
    """Onboarding-time measurement: manual vs tool arm."""

    __tablename__ = "timings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    participant: Mapped[str] = mapped_column(Text, nullable=False)
    arm: Mapped[str] = mapped_column(Text, nullable=False)  # "manual" or "tool"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fields_corrected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)

    document: Mapped[Document] = relationship(back_populates="timings")

    __table_args__ = (Index("ix_timings_document", "document_id"),)
