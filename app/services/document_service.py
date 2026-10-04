"""Document lifecycle: validation, storage, registry updates, and job scheduling."""

from __future__ import annotations

import hashlib
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, event, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.domain import TERMINAL_FAILURES, DocumentStatus, FileType
from app.exceptions import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    UnsupportedFileTypeError,
    ValidationError,
)
from app.models.database import session_scope
from app.models.document import Document
from app.models.knowledge import ChunkRecord, EntityRecord, SectionRecord
from app.parsers.base import ParserRegistry
from app.schemas.document import DocumentMetadataInput, LibraryStats
from app.services.ingestion import IngestionPipeline
from app.services.keyword_index import KeywordIndex
from app.services.knowledge_store import KnowledgeStore
from app.services.vector_store import QdrantVectorStore

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(
        self,
        settings: Settings,
        pipeline: IngestionPipeline,
        registry: ParserRegistry,
        store: QdrantVectorStore,
        knowledge: KnowledgeStore,
        keyword_index: KeywordIndex,
    ) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.registry = registry
        self.store = store
        self.knowledge = knowledge
        self.keyword_index = keyword_index
        self._executor = ThreadPoolExecutor(
            max_workers=settings.ingestion_workers, thread_name_prefix="ingest"
        )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def upload(
        self,
        session: Session,
        filename: str,
        content: bytes,
        metadata: DocumentMetadataInput,
        owner_id: str | None = None,
    ) -> Document:
        filename = Path(filename).name
        extension = Path(filename).suffix.lower()

        accepted = self.registry.effective_extensions(self.settings.allowed_extensions)
        if extension not in accepted:
            raise UnsupportedFileTypeError(
                f"الامتداد '{extension or 'غير معروف'}' غير مدعوم. "
                f"المدعوم حاليًا: {', '.join(accepted)}"
            )
        if not content.strip():
            raise ValidationError("الملف فارغ.")
        if len(content) > self.settings.max_upload_bytes:
            limit_mb = self.settings.max_upload_bytes / (1024 * 1024)
            raise ValidationError(f"حجم الملف يتجاوز الحد المسموح ({limit_mb:.0f} ميجابايت).")

        content_hash = hashlib.sha256(content).hexdigest()

        # Every failed state, not just the original one. Splitting `failed` into
        # `failed_parsing`, `ocr_required` and the rest meant a document that failed for
        # one of the new reasons no longer matched this exclusion, so re-uploading a
        # corrected file was refused as a duplicate of the broken one.
        identical = session.scalar(
            select(Document).where(
                Document.content_hash == content_hash,
                Document.status.not_in(list(TERMINAL_FAILURES)),
                # Among this owner's documents only. Checked across everyone, the refusal
                # named another person's file — and kept them from adding their own copy.
                Document.owner_id.is_(None) if owner_id is None else Document.owner_id == owner_id,
            )
        )
        if identical is not None:
            raise DuplicateDocumentError(
                f"هذا المحتوى مفهرس بالفعل ضمن المستند '{identical.filename}' "
                f"(id={identical.id}). استخدم إعادة الفهرسة أو احذفه أولًا."
            )

        # A file is the same document only in the same place: two projects, or two
        # folders of one project, each keep their own "Letter 01.pdf".
        project = (metadata.project or "").strip()
        folder = (metadata.folder or "").strip().strip("/")
        existing = session.scalar(
            select(Document).where(
                Document.filename == filename,
                Document.project == project,
                Document.folder == folder,
                Document.owner_id.is_(None) if owner_id is None else Document.owner_id == owner_id,
            )
        )
        document = existing or Document(id=str(uuid.uuid4()), filename=filename)

        stored_path = self.settings.upload_dir / f"{document.id}{extension}"
        stored_path.write_bytes(content)

        overrides = metadata.model_dump(exclude_none=True)
        document.stored_path = str(stored_path)
        document.content_hash = content_hash
        document.size_bytes = len(content)
        document.status = DocumentStatus.UPLOADED
        document.error_message = None
        document.chunk_count = 0
        document.indexed_at = None
        document.uploaded_at = datetime.now(UTC)
        document.extra_metadata = {**(document.extra_metadata or {}), "overrides": overrides}
        if overrides.get("title"):
            document.title = overrides["title"]
        document.category = overrides.get("category") or document.category or self.settings.default_category
        document.project = project
        document.folder = folder
        document.owner_id = owner_id

        session.add(document)
        session.flush()
        document_id = document.id

        if existing is not None:
            logger.info("Replacing existing document %s (%s) with a new version", document_id, filename)

        self._schedule_after_commit(session, document_id)
        return document

    def _schedule_after_commit(self, session: Session, document_id: str) -> None:
        """The worker opens its own session, so it must not start before this row is committed."""
        event.listen(
            session,
            "after_commit",
            lambda _session: self._executor.submit(self.pipeline.run, document_id),
            once=True,
        )

    def list_documents(
        self,
        session: Session,
        status: DocumentStatus | None = None,
        category: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
        project: str | None = None,
        owner_id: str | None = None,
    ) -> tuple[int, list[Document]]:
        filters = []
        if owner_id is not None:
            filters.append(Document.owner_id == owner_id)
        if project is not None:
            filters.append(Document.project == project)
        if status is not None:
            filters.append(Document.status == status)
        if category:
            filters.append(Document.category == category)
        if search:
            pattern = f"%{search}%"
            filters.append(or_(Document.filename.ilike(pattern), Document.title.ilike(pattern)))

        total = session.scalar(select(func.count()).select_from(Document).where(*filters)) or 0
        items = list(
            session.scalars(
                select(Document)
                .where(*filters)
                .order_by(Document.uploaded_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return total, items

    def get(self, session: Session, document_id: str) -> Document:
        document = session.get(Document, document_id)
        if document is None:
            raise DocumentNotFoundError(f"لا يوجد مستند بالمعرف '{document_id}'.")
        return document

    def get_chunks(self, document_id: str, limit: int = 500) -> list[dict]:
        return self.store.list_document_chunks(document_id, limit=limit)

    def entities(self, document_id: str, kind: str | None = None, limit: int = 200) -> list:
        with session_scope() as session:
            statement = select(EntityRecord).where(EntityRecord.document_id == document_id)
            if kind:
                statement = statement.where(EntityRecord.kind == kind)
            return list(session.scalars(statement.limit(limit)))

    def read_stored_file(self, document: Document) -> str:
        """The document's text, for showing a citation at its source.

        For Markdown this is the file itself, unchanged. For a binary format there is no
        such thing as "the file as text" — decoding a PDF would produce pages of noise
        that looks like a failure of this system rather than a category error — so the
        normalised text is reassembled from the stored sections instead. It is the same
        text the answer was actually grounded in, which makes it the more honest thing
        to show anyway.
        """
        stored = Path(document.stored_path)
        if not stored.exists():
            raise DocumentNotFoundError(
                f"الملف الأصلي للمستند '{document.filename}' غير موجود على القرص."
            )
        if (document.file_type or FileType.MARKDOWN) == FileType.MARKDOWN:
            return stored.read_text(encoding="utf-8", errors="replace")
        return self._rebuild_text(document.id)

    @staticmethod
    def _rebuild_text(document_id: str) -> str:
        """The normalised document, rebuilt from its sections in document order."""
        with session_scope() as session:
            sections = session.scalars(
                select(SectionRecord)
                .where(SectionRecord.document_id == document_id)
                .order_by(SectionRecord.order_index)
            ).all()
            chunks = session.scalars(
                select(ChunkRecord)
                .where(ChunkRecord.document_id == document_id)
                .order_by(ChunkRecord.chunk_index)
            ).all()

            by_section: dict[str, list[str]] = {}
            for chunk in chunks:
                by_section.setdefault(chunk.section_id, []).append(chunk.content)

            parts: list[str] = []
            for section in sections:
                if section.heading:
                    parts.append("#" * max(section.level, 1) + f" {section.heading}")
                parts.extend(by_section.get(section.section_id, []))
            return "\n\n".join(p for p in parts if p.strip())

    def library_stats(self, session: Session, owner_id: str | None = None) -> LibraryStats:
        """Totals over the library — or one owner's part of it — counted in the database."""
        mine = [Document.owner_id == owner_id] if owner_id is not None else []
        by_status = {
            str(status): int(count)
            for status, count in session.execute(
                select(Document.status, func.count()).where(*mine).group_by(Document.status)
            )
        }
        totals = session.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.chunk_count), 0),
                func.coalesce(func.sum(Document.char_count), 0),
                func.coalesce(func.sum(Document.size_bytes), 0),
                func.max(Document.indexed_at),
                func.max(Document.uploaded_at),
            ).where(*mine)
        ).one()
        in_flight = {
            DocumentStatus.UPLOADED, DocumentStatus.PARSING, DocumentStatus.ANALYZING,
            DocumentStatus.CHUNKING, DocumentStatus.EMBEDDING, DocumentStatus.INDEXING,
        }
        return LibraryStats(
            documents=int(totals[0] or 0),
            by_status=by_status,
            processing=sum(by_status.get(str(s), 0) for s in in_flight),
            failed=by_status.get(str(DocumentStatus.FAILED), 0),
            chunks=int(totals[1] or 0),
            characters=int(totals[2] or 0),
            bytes=int(totals[3] or 0),
            categories=len(self.categories(session, owner_id)),
            last_ingested_at=totals[4],
            last_uploaded_at=totals[5],
        )

    def categories(self, session: Session, owner_id: str | None = None) -> list[str]:
        query = select(Document.category).distinct().order_by(Document.category)
        if owner_id is not None:
            query = query.where(Document.owner_id == owner_id)
        rows = session.scalars(query)
        return [row for row in rows if row]

    def projects(self, session: Session, owner_id: str | None = None) -> list[str]:
        query = select(Document.project).distinct().order_by(Document.project)
        if owner_id is not None:
            query = query.where(Document.owner_id == owner_id)
        rows = session.scalars(query)
        return [row for row in rows if row]

    def delete(self, session: Session, document_id: str) -> None:
        document = self.get(session, document_id)
        # First, before anything is removed: a job still processing this document must
        # not write it back after the delete. The job checks before each write and takes
        # back out what it wrote since.
        self.pipeline.cancel(document_id)
        self.store.delete_document(document_id)
        self.knowledge.purge(session, document_id)
        event.listen(
            session, "after_commit", lambda _s: self.keyword_index.rebuild(), once=True
        )

        stored = Path(document.stored_path)
        if stored.exists():
            try:
                stored.unlink()
            except OSError as exc:
                logger.warning("Could not remove stored file %s: %s", stored, exc)

        session.delete(document)

    def remove_orphans(self) -> dict[str, int]:
        """Remove what deleted documents left behind: knowledge rows and vector points
        whose document no longer exists. Run at startup, so leftovers from before the
        delete guard — or from a crash mid-delete — do not outlive a restart."""
        with session_scope() as session:
            live = set(session.scalars(select(Document.id)))
            removed = {"chunks": 0, "sections": 0, "entities": 0}
            for key, model in (("chunks", ChunkRecord), ("sections", SectionRecord), ("entities", EntityRecord)):
                result = session.execute(delete(model).where(model.document_id.not_in(select(Document.id))))
                removed[key] = result.rowcount or 0

        orphaned = self.store.document_ids() - live
        for document_id in orphaned:
            self.store.delete_document(document_id)
        removed["documents_with_points"] = len(orphaned)

        if any(removed.values()):
            logger.info("Removed leftovers of deleted documents: %s", removed)
            self.keyword_index.rebuild()
        return removed

    def reindex(self, session: Session, document_id: str) -> Document:
        document = self.get(session, document_id)
        if not Path(document.stored_path).exists():
            raise ValidationError(
                f"الملف الأصلي للمستند '{document.filename}' غير موجود على القرص، لا يمكن إعادة الفهرسة."
            )
        document.status = DocumentStatus.UPLOADED
        document.error_message = None
        session.flush()
        self._schedule_after_commit(session, document_id)
        return document
