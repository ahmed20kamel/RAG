"""Parser-agnostic domain objects shared by parsers, chunker and stores."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    PARSING = "parsing"
    ANALYZING = "analyzing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"

    # Failures worth telling apart. "It failed" sends someone to the logs; "this file is
    # a scan and needs OCR" tells them what to do about it. The column is free text, so
    # these cost no migration — but every one of them must still carry a message.
    UNSUPPORTED_FORMAT = "unsupported_format"
    FAILED_EXTRACTION = "failed_extraction"
    FAILED_PARSING = "failed_parsing"
    OCR_REQUIRED = "ocr_required"
    FAILED_EMBEDDING = "failed_embedding"


#: Every status that means the document is not, and will not become, retrievable without
#: someone intervening. Collected here so no caller has to enumerate them again.
TERMINAL_FAILURES = frozenset({
    DocumentStatus.FAILED,
    DocumentStatus.UNSUPPORTED_FORMAT,
    DocumentStatus.FAILED_EXTRACTION,
    DocumentStatus.FAILED_PARSING,
    DocumentStatus.OCR_REQUIRED,
    DocumentStatus.FAILED_EMBEDDING,
})


class DocumentType(StrEnum):
    LEGAL_CASE = "legal_case"
    CONTRACT = "contract"
    PROCEDURE = "procedure"
    METHOD_STATEMENT = "method_statement"
    REPORT = "report"
    GENERAL = "general"


class FileType(StrEnum):
    """What the bytes actually were, as detected — not as the filename claimed."""

    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    IMAGE = "image"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class SourceLocation:
    """Where something sits in the original file.

    Filled in by the parser, which is the only layer that can still see the file. Nothing
    downstream may infer, adjust or recompute these: a citation that shifts between two
    runs over the same document is worse than no citation, because it looks reliable.

    Every field is optional because the formats disagree about what a location even is —
    a page means nothing in a spreadsheet, a sheet means nothing in a PDF.
    """

    page: int | None = None
    sheet: str = ""
    row_start: int | None = None
    row_end: int | None = None
    table_index: int | None = None
    paragraph_index: int | None = None

    def locator(self) -> str:
        """The phrase shown in a citation, or empty when the format has no locator.

        Empty is the Markdown case, and it is load-bearing: an empty locator makes every
        caller fall through to the wording it used before this existed, which is how the
        existing corpus keeps citing itself exactly as it always has.
        """
        if self.sheet:
            where = f"Sheet: {self.sheet}"
            if self.row_start is not None:
                rows = (
                    f"{self.row_start}"
                    if self.row_end in (None, self.row_start)
                    else f"{self.row_start}–{self.row_end}"
                )
                where += f" — صفوف {rows}"
            return where
        if self.page is not None:
            where = f"صفحة {self.page}"
            if self.table_index is not None:
                where += f" — جدول {self.table_index}"
            return where
        if self.table_index is not None:
            return f"جدول {self.table_index}"
        return ""


class BlockType(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE = "table"
    LIST = "list"
    IMAGE = "image"
    SHEET_RANGE = "sheet_range"


@dataclass(slots=True)
class Block:
    """One piece of a section, kept in two forms on purpose.

    `text` is Markdown, and it is what the rest of the system reads: the chunker splits
    on Markdown conventions, the structure analyzer detects tables by them, and the
    entity extractor pulls label/value facts out of Markdown table rows. Emitting
    Markdown from every parser is what lets a spreadsheet row become a cited fact
    without a single change to any of them.

    `structured_data` is the same content before it was flattened, so a number that was
    a number in the file is still a number here. Rendering is lossy; this is the copy
    that is not.
    """

    type: str = BlockType.PARAGRAPH
    text: str = ""
    structured_data: list[list[str]] | None = None
    location: SourceLocation | None = None
    order: int = 0


@dataclass(slots=True)
class Section:
    """A heading, the body text under it, and its place in the heading tree."""

    heading: str
    level: int
    content: str
    path: list[str] = field(default_factory=list)
    order: int = 0

    section_id: str = ""
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)

    has_table: bool = False
    has_list: bool = False
    has_code: bool = False
    terms: list[str] = field(default_factory=list)
    summary: str = ""

    #: The structured form of `content`, when a parser had one to give. Markdown leaves
    #: it empty and nothing downstream requires it, so the existing path is untouched.
    blocks: list[Block] = field(default_factory=list)
    location: SourceLocation | None = None

    @property
    def breadcrumb(self) -> str:
        return " → ".join(self.path) if self.path else self.heading

    @property
    def locator(self) -> str:
        return self.location.locator() if self.location else ""


@dataclass(slots=True)
class Entity:
    """A fact extracted verbatim from the document. Never inferred, never generated."""

    kind: str
    value: str
    normalized: str
    section_id: str
    context: str
    label: str = ""


@dataclass(slots=True)
class ParsedDocument:
    """Normalised output of any DocumentParser implementation."""

    title: str
    sections: list[Section]
    raw_text: str
    language: str
    metadata: dict[str, object] = field(default_factory=dict)
    document_type: str = DocumentType.GENERAL

    file_type: str = FileType.MARKDOWN
    #: Set by a parser that produced text it does not trust — a PDF whose extraction came
    #: back mostly unreadable, say. It travels to the reader instead of being swallowed,
    #: because a citation to mangled text should look different from a citation to good text.
    extraction_warning: str = ""


@dataclass(slots=True)
class Chunk:
    """A retrievable unit with the provenance needed to cite it."""

    chunk_id: str
    document_id: str
    filename: str
    index: int
    heading: str
    section: str
    content: str
    embed_text: str
    heading_level: int
    char_count: int

    section_id: str = ""
    parent_section_id: str | None = None
    parent_section: str = ""
    document_title: str = ""
    has_table: bool = False
    has_list: bool = False
    has_code: bool = False

    #: Frozen at parse time and carried unchanged to the citation. Empty for Markdown,
    #: which is what keeps every existing citation byte-identical.
    locator: str = ""
    page: int | None = None
