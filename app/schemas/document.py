"""Request/response contracts for the documents API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import DocumentStatus


class DocumentMetadataInput(BaseModel):
    """Optional overrides supplied at upload time; front matter fills the gaps."""

    title: str | None = Field(default=None, max_length=512)
    category: str | None = Field(default=None, max_length=128)
    source: str | None = Field(default=None, max_length=512)
    version: str | None = Field(default=None, max_length=64)
    date: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=16)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    title: str
    category: str
    source: str
    version: str
    doc_date: str
    language: str
    #: What the bytes were detected to be. Existing rows read 'markdown'.
    file_type: str = "markdown"
    status: DocumentStatus
    error_message: str | None
    chunk_count: int
    char_count: int
    size_bytes: int
    extra_metadata: dict
    uploaded_at: datetime
    updated_at: datetime
    indexed_at: datetime | None


class DocumentListResponse(BaseModel):
    total: int
    items: list[DocumentResponse]


class DocumentChunkPreview(BaseModel):
    chunk_id: str
    index: int
    heading: str
    section: str
    char_count: int
    content: str


class DocumentDetailResponse(DocumentResponse):
    chunks: list[DocumentChunkPreview] = Field(default_factory=list)


class SectionResponse(BaseModel):
    section_id: str
    heading: str
    path: str
    level: int
    parent_id: str | None
    child_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    terms: list[str] = Field(default_factory=list)
    has_table: bool = False
    has_list: bool = False
    has_code: bool = False
    char_count: int = 0


class EntityResponse(BaseModel):
    kind: str
    value: str
    label: str = ""
    section_id: str = ""
    context: str = ""


class DeleteResponse(BaseModel):
    id: str
    deleted: bool
    message: str


class DocumentChunkPage(BaseModel):
    """One page of a document's chunks, so a large document need not load at once."""

    total: int
    offset: int
    items: list[DocumentChunkPreview] = Field(default_factory=list)


class DocumentRaw(BaseModel):
    """The stored file as it was uploaded — what a citation ultimately points at."""

    id: str
    filename: str
    content: str
    char_count: int


class LibraryStats(BaseModel):
    """Counts for the knowledge-base overview, without listing every document."""

    documents: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    processing: int = 0
    failed: int = 0
    chunks: int = 0
    characters: int = 0
    bytes: int = 0
    categories: int = 0
    last_ingested_at: datetime | None = None
    last_uploaded_at: datetime | None = None
