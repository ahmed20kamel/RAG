"""Ingestion pipeline: Parsing → Chunking → Embedding → Indexing → Completed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.core.domain import DocumentStatus, ParsedDocument
from app.exceptions import (
    EmbeddingError,
    ExtractionError,
    OcrRequiredError,
    ParsingError,
    RagError,
    UnsupportedFileTypeError,
)
from app.models.database import session_scope
from app.models.document import Document
from app.parsers.base import ParserRegistry
from app.parsers.detection import detect
from app.services.chunking import HeadingAwareChunker
from app.services.embeddings import OllamaEmbeddingClient
from app.services.entities import EntityExtractor
from app.services.keyword_index import KeywordIndex
from app.services.knowledge_store import KnowledgeStore
from app.services.structure import DocumentStructureAnalyzer
from app.services.summaries import SectionSummarizer
from app.services.vector_store import QdrantVectorStore

logger = logging.getLogger(__name__)


class IngestionPipeline:
    def __init__(
        self,
        registry: ParserRegistry,
        chunker: HeadingAwareChunker,
        embedder: OllamaEmbeddingClient,
        store: QdrantVectorStore,
        structure: DocumentStructureAnalyzer,
        entity_extractor: EntityExtractor,
        summarizer: SectionSummarizer,
        knowledge: KnowledgeStore,
        keyword_index: KeywordIndex,
    ) -> None:
        self.registry = registry
        self.chunker = chunker
        self.embedder = embedder
        self.store = store
        self.structure = structure
        self.entity_extractor = entity_extractor
        self.summarizer = summarizer
        self.knowledge = knowledge
        self.keyword_index = keyword_index

    def run(self, document_id: str) -> None:
        """Process one document end to end. Never raises: failures land in the DB row."""
        try:
            self._run(document_id)
        except RagError as exc:
            logger.warning("Ingestion failed for %s: %s", document_id, exc.message)
            self._fail(document_id, exc.message, self._status_for(exc))
        except Exception as exc:  # noqa: BLE001 - last line of defence for a worker thread
            logger.exception("Unexpected ingestion failure for %s", document_id)
            self._fail(document_id, f"خطأ غير متوقع أثناء المعالجة: {exc}")

    @staticmethod
    def _status_for(error: RagError) -> DocumentStatus:
        """The failure state that tells the reader what to do next.

        Ordered most specific first, because these subclass each other: an OCR failure
        is an extraction failure is a parsing failure, and collapsing them back into a
        single "failed" would throw away the only part anyone can act on.
        """
        if isinstance(error, OcrRequiredError):
            return DocumentStatus.OCR_REQUIRED
        if isinstance(error, ExtractionError):
            return DocumentStatus.FAILED_EXTRACTION
        if isinstance(error, UnsupportedFileTypeError):
            return DocumentStatus.UNSUPPORTED_FORMAT
        if isinstance(error, ParsingError):
            return DocumentStatus.FAILED_PARSING
        if isinstance(error, EmbeddingError):
            return DocumentStatus.FAILED_EMBEDDING
        return DocumentStatus.FAILED

    def _run(self, document_id: str) -> None:
        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is None:
                logger.warning("Document %s disappeared before ingestion", document_id)
                return
            stored_path = Path(document.stored_path)
            filename = document.filename
            overrides = dict(document.extra_metadata.get("overrides") or {})

        if not stored_path.exists():
            raise RagError(f"الملف المخزن غير موجود: {stored_path}")

        self._set_status(document_id, DocumentStatus.VALIDATING)
        extension = stored_path.suffix.lower()
        content = stored_path.read_bytes()

        # The extension was checked at upload; this checks the bytes. Doing it again here
        # is deliberate — the file has been sitting on disk in between, and the parser
        # about to open it should be chosen by what the file is, not by what it is named.
        detected = detect(content, extension)
        if detected.corrupt:
            # Checked before the extension mismatch: a damaged container reads as an
            # unknown type, and reporting it as the wrong format would send someone to
            # convert a file that simply needs re-exporting.
            raise ParsingError(f"تعذر قراءة الملف '{filename}'. {detected.detail}".strip())
        if not detected.agrees:
            raise UnsupportedFileTypeError(
                f"محتوى الملف '{filename}' لا يطابق امتداده '{extension}'. "
                f"{detected.detail}".strip()
            )

        parser = self.registry.get(extension)
        if parser is None:
            raise UnsupportedFileTypeError(f"لا يوجد محلل مسجل للامتداد '{extension}'.")

        self._set_status(document_id, DocumentStatus.PARSING)
        parsed = parser.parse(content, filename)
        logger.info(
            "Parsed %s (%s) into %s sections",
            filename, parsed.file_type, len(parsed.sections),
        )
        if parsed.extraction_warning:
            logger.warning("%s: %s", filename, parsed.extraction_warning)

        self._set_status(document_id, DocumentStatus.ANALYZING)
        self.structure.analyze(parsed)
        self.summarizer.apply(parsed.sections)
        entities = self.entity_extractor.extract(parsed.sections)
        logger.info(
            "Analyzed %s: type=%s, %s sections, %s entities",
            filename, parsed.document_type, len(parsed.sections), len(entities),
        )

        self._set_status(document_id, DocumentStatus.CHUNKING)
        chunks = self.chunker.chunk(parsed, document_id, filename)
        if not chunks:
            raise RagError(f"لم ينتج عن الملف '{filename}' أي chunks قابلة للفهرسة.")
        logger.info("Chunked %s into %s chunks", filename, len(chunks))

        metadata = self._resolve_metadata(parsed, overrides)

        self.knowledge.replace_document(
            document_id=document_id,
            sections=parsed.sections,
            entities=entities,
            chunks=chunks,
            category=metadata["category"],
            version=metadata["version"],
            language=metadata["language"],
        )

        self._set_status(document_id, DocumentStatus.EMBEDDING)
        vectors = self.embedder.embed([chunk.embed_text for chunk in chunks])

        self._set_status(document_id, DocumentStatus.INDEXING)
        self.store.ensure_collection(len(vectors[0]))
        self.store.delete_document(document_id)
        payload_extra = {
            "title": metadata["title"],
            "category": metadata["category"],
            "source": metadata["source"],
            "version": metadata["version"],
            "date": metadata["date"],
            "language": metadata["language"],
        }
        indexed = self.store.upsert_chunks(chunks, vectors, payload_extra)
        self.keyword_index.rebuild()
        logger.info("Indexed %s points for %s", indexed, filename)

        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is None:
                return
            document.title = metadata["title"] or document.title
            document.category = metadata["category"]
            document.source = metadata["source"]
            document.version = metadata["version"]
            document.doc_date = metadata["date"]
            document.language = metadata["language"]
            document.chunk_count = indexed
            document.char_count = len(parsed.raw_text)
            document.file_type = str(parsed.file_type)
            document.status = DocumentStatus.COMPLETED
            document.error_message = None
            document.indexed_at = datetime.now(UTC)
            document.extra_metadata = {
                **document.extra_metadata,
                "sections": len(parsed.sections),
                "entities": len(entities),
                "document_type": str(parsed.document_type),
                "front_matter": {k: str(v) for k, v in parsed.metadata.items()},
                "parser": parser.name,
                # The parser's own findings — page counts, sheet counts, how many
                # pages needed OCR — kept beside the document's declared metadata
                # so the library can show them without re-opening the file.
                **{k: v for k, v in parsed.metadata.items() if k in ("pages", "sheets", "ocr_pages")},
                # Kept on the row rather than only in the log, so a reader who sees a
                # citation into this document can be told the text was doubtful.
                "extraction_warning": parsed.extraction_warning,
            }

    def _resolve_metadata(self, parsed: ParsedDocument, overrides: dict) -> dict[str, str]:
        """Upload-time overrides win, then front matter, then values derived from content."""
        front = parsed.metadata

        def pick(key: str, fallback: str) -> str:
            for candidate in (overrides.get(key), front.get(key)):
                if candidate not in (None, ""):
                    return str(candidate).strip()
            return fallback

        language = pick("language", parsed.language)
        if language == "auto":
            language = parsed.language

        return {
            "title": pick("title", parsed.title),
            "category": pick("category", "General"),
            "source": pick("source", ""),
            "version": pick("version", "1.0"),
            "date": pick("date", ""),
            "language": language,
        }

    @staticmethod
    def _set_status(document_id: str, status: DocumentStatus) -> None:
        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is not None:
                document.status = status
                document.error_message = None

    def _fail(
        self,
        document_id: str,
        message: str,
        status: DocumentStatus = DocumentStatus.FAILED,
    ) -> None:
        try:
            # Drop any knowledge rows written before the failure, so a document marked
            # failed can never be retrieved or cited.
            self.knowledge.delete_document(document_id)
            self.keyword_index.rebuild()
        except Exception:  # noqa: BLE001
            logger.exception("Could not clean knowledge rows for %s", document_id)

        try:
            with session_scope() as session:
                document = session.get(Document, document_id)
                if document is not None:
                    document.status = status
                    document.error_message = message[:2000]
        except Exception:  # noqa: BLE001
            logger.exception("Could not persist failure state for %s", document_id)
