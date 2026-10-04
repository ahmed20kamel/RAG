"""Structured knowledge index: sections, extracted entities, and chunk text.

Qdrant holds the vectors; this holds everything that is better answered with SQL —
section hierarchy, verbatim facts, and the text the keyword index scores over.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class SectionRecord(Base):
    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    section_id: Mapped[str] = mapped_column(String(16), nullable=False)

    heading: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    parent_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    child_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    terms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    has_table: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_list: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_code: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class EntityRecord(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    section_id: Mapped[str] = mapped_column(String(16), index=True, nullable=False)

    kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    label: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    # Normalised label + value, so a query worded differently still matches.
    search_text: Mapped[str] = mapped_column(String(1024), index=True, nullable=False)
    context: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ChunkRecord(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    section_id: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    filename: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    document_title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    heading: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    section: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parent_section: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parent_section_id: Mapped[str | None] = mapped_column(String(16), nullable=True)

    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_table: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_list: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_code: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Where this chunk came from inside the original file, frozen at parse time.
    # Empty for Markdown, which has no location finer than its heading path.
    locator: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)

    category: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="", nullable=False)
