"""Document registry record. Vectors live in Qdrant; this table tracks state and metadata."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.domain import DocumentStatus
from app.models.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(128), default="General", index=True, nullable=False)
    #: The project a file belongs to and the folder it came from, both read from the
    #: folder it was uploaded in. Together with the filename they identify a document:
    #: two projects each keep their own "Letter 01.pdf". Empty for a single-file upload.
    project: Mapped[str] = mapped_column(String(256), default="", index=True, nullable=False)
    folder: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    #: Who uploaded it. A document is visible to its owner and to whoever may read
    #: every document; one without an owner (uploaded before owners existed) only to
    #: the latter.
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="1.0", nullable=False)
    doc_date: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)

    # What the bytes turned out to be, recorded at upload rather than re-derived from the
    # filename later. Existing rows default to markdown, which is what they are.
    file_type: Mapped[str] = mapped_column(
        String(16), default="markdown", index=True, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(32), default=DocumentStatus.UPLOADED, index=True, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Explicit authority metadata, set by an operator. Nothing is inferred from the
    # filename or the text: these are the only fields allowed to settle a
    # disagreement between two documents, and an undeclared value settles nothing.
    authority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    doc_status: Mapped[str] = mapped_column(
        String(32), default="active", index=True, nullable=False
    )
    effective_date: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
