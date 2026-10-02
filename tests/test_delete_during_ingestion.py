"""A document deleted while it is being processed stays deleted.

The pipeline read the document once, at the start, and never looked again. A contract
deleted mid-processing was then written into the knowledge tables and the vector index
a minute after the delete, and its text stayed findable by keyword. This deletes a
document at each step of the pipeline and checks that nothing of it survives — and that
a document nobody deleted is still processed exactly as before.

Offline: a temporary database and upload folder, an in-memory vector store, and an
embedder that waits for the test, so "during processing" is a chosen moment, not a race.

Run: python tests/test_delete_during_ingestion.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-delete-race-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(WORKDIR / "uploads")
(WORKDIR / "uploads").mkdir()

from sqlalchemy import func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models.database import engine, init_database, session_scope  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.knowledge import ChunkRecord, EntityRecord, SectionRecord  # noqa: E402
from app.parsers.base import ParserRegistry  # noqa: E402
from app.parsers.markdown_parser import MarkdownParser  # noqa: E402
from app.schemas.document import DocumentMetadataInput  # noqa: E402
from app.services.chunking import HeadingAwareChunker  # noqa: E402
from app.services.document_service import DocumentService  # noqa: E402
from app.services.entities import EntityExtractor  # noqa: E402
from app.services.ingestion import IngestionPipeline  # noqa: E402
from app.services.keyword_index import KeywordIndex  # noqa: E402
from app.services.knowledge_store import KnowledgeStore  # noqa: E402
from app.services.structure import DocumentStructureAnalyzer  # noqa: E402
from app.services.summaries import SectionSummarizer  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


class MemoryStore:
    """The vector store's surface the pipeline and the service use, kept in a dict."""

    def __init__(self) -> None:
        self.points: dict[str, str] = {}  # chunk_id -> document_id
        self.written: list[str] = []  # every document an upsert was made for, in order
        self.lock = threading.Lock()

    def ensure_collection(self, dimension: int) -> None:
        pass

    def upsert_chunks(self, chunks, vectors, payload_extra) -> int:
        with self.lock:
            for chunk in chunks:
                self.points[chunk.chunk_id] = chunk.document_id
            self.written.extend({chunk.document_id for chunk in chunks})
        return len(chunks)

    def delete_document(self, document_id: str) -> None:
        with self.lock:
            for key in [k for k, v in self.points.items() if v == document_id]:
                del self.points[key]

    def document_ids(self) -> set[str]:
        with self.lock:
            return set(self.points.values())

    def of(self, document_id: str) -> int:
        with self.lock:
            return sum(1 for v in self.points.values() if v == document_id)


class GatedEmbedder:
    """Stops at the embedding step until the test lets it continue."""

    dimension = 4

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        self.gated = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.gated:
            self.reached.set()
            self.release.wait(30)
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class GatedIndexStore(MemoryStore):
    """Stops just after writing the points, the last moment before completion."""

    def __init__(self) -> None:
        super().__init__()
        self.reached = threading.Event()
        self.release = threading.Event()
        self.gated = False

    def upsert_chunks(self, chunks, vectors, payload_extra) -> int:
        written = super().upsert_chunks(chunks, vectors, payload_extra)
        if self.gated:
            self.reached.set()
            self.release.wait(30)
        return written


DOCUMENT = """# عقد الدفعة الأولى

## البند الأول — قيمة العقد

قيمة العقد 1,250,000 درهم إماراتي تُدفع على ثلاث دفعات. الدفعة الأولى 25,000 درهم عند التوقيع.

## البند الثاني — المدة

مدة التنفيذ اثنا عشر شهرًا من تاريخ استلام الموقع، ويلتزم المقاول بالبرنامج الزمني المعتمد.
"""


def build(store: MemoryStore, embedder) -> tuple[DocumentService, IngestionPipeline, KeywordIndex]:
    settings = get_settings()
    registry = ParserRegistry([MarkdownParser(settings.max_heading_depth)])
    knowledge = KnowledgeStore()
    keyword_index = KeywordIndex()
    pipeline = IngestionPipeline(
        registry=registry,
        chunker=HeadingAwareChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            min_chunk_size=settings.min_chunk_size,
        ),
        embedder=embedder,
        store=store,
        structure=DocumentStructureAnalyzer(),
        entity_extractor=EntityExtractor(),
        summarizer=SectionSummarizer(),
        knowledge=knowledge,
        keyword_index=keyword_index,
    )
    service = DocumentService(
        settings=settings, pipeline=pipeline, registry=registry,
        store=store, knowledge=knowledge, keyword_index=keyword_index,
    )
    return service, pipeline, keyword_index


def upload(service: DocumentService, name: str) -> str:
    with session_scope() as session:
        document = service.upload(
            # Each upload differs by a line, since identical content is refused as a duplicate.
            session=session, filename=name, content=f"{DOCUMENT}\nمرجع النسخة: {name}\n".encode("utf-8"),
            metadata=DocumentMetadataInput(),
        )
        return document.id


def delete(service: DocumentService, document_id: str) -> None:
    with session_scope() as session:
        service.delete(session, document_id)


def leftovers(document_id: str) -> dict[str, int]:
    with session_scope() as session:
        return {
            model.__tablename__: session.scalar(
                select(func.count()).select_from(model).where(model.document_id == document_id)
            )
            for model in (ChunkRecord, SectionRecord, EntityRecord)
        }


def status(document_id: str) -> str | None:
    with session_scope() as session:
        document = session.get(Document, document_id)
        return str(document.status) if document else None


def found_by_keyword(keyword_index: KeywordIndex, document_id: str) -> bool:
    hits = keyword_index.search("الدفعة الأولى قيمة العقد", limit=50, document_ids=[document_id])
    return bool(hits)


def wait_idle(service: DocumentService) -> None:
    service._executor.shutdown(wait=True)


def main() -> int:
    init_database()

    print("\n=== 1. untouched document: processed exactly as before ===")
    store = MemoryStore()
    service, _, keyword_index = build(store, GatedEmbedder())
    kept = upload(service, "kept.md")
    wait_idle(service)
    check(status(kept) == "completed", "an undeleted document completes", str(status(kept)))
    check(store.of(kept) > 0, "its points are in the index")
    check(leftovers(kept)["chunks"] > 0, "its chunks are in the knowledge tables")
    check(found_by_keyword(keyword_index, kept), "it is found by keyword")

    print("\n=== 2. deleted while embedding (mid-processing) ===")
    store = MemoryStore()
    embedder = GatedEmbedder()
    embedder.gated = True
    service, _, keyword_index = build(store, embedder)
    doomed = upload(service, "contract-embedding.md")
    check(embedder.reached.wait(30), "the job reached the embedding step")
    delete(service, doomed)
    embedder.release.set()
    wait_idle(service)
    keyword_index.rebuild()
    check(status(doomed) is None, "the document row is gone")
    check(doomed not in store.written, "the index was never written for it after the delete")
    check(store.of(doomed) == 0, "no points remain", f"{store.of(doomed)} points")
    check(not any(leftovers(doomed).values()), "no knowledge rows remain", str(leftovers(doomed)))
    check(not found_by_keyword(keyword_index, doomed), "its text is not found by keyword")

    print("\n=== 3. deleted after its points were written, before completion ===")
    store = GatedIndexStore()
    store.gated = True
    service, _, keyword_index = build(store, GatedEmbedder())
    late = upload(service, "contract-indexing.md")
    check(store.reached.wait(30), "the job wrote its points and is about to complete")
    delete(service, late)
    store.release.set()
    wait_idle(service)
    keyword_index.rebuild()
    check(status(late) is None, "the document row is gone")
    check(store.of(late) == 0, "the points it had written are taken back out", f"{store.of(late)} points")
    check(not any(leftovers(late).values()), "no knowledge rows remain", str(leftovers(late)))
    check(not found_by_keyword(keyword_index, late), "its text is not found by keyword")

    print("\n=== 4. leftovers from before the fix are removed at startup ===")
    store = MemoryStore()
    service, _, keyword_index = build(store, GatedEmbedder())
    ghost = upload(service, "ghost.md")
    wait_idle(service)
    # The old failure, reproduced by hand: the row is gone, everything else stayed.
    with session_scope() as session:
        session.delete(session.get(Document, ghost))
    check(any(leftovers(ghost).values()) and store.of(ghost) > 0, "leftovers exist before cleanup")
    keyword_index.rebuild()
    check(found_by_keyword(keyword_index, ghost), "and its text is still found by keyword")
    service, _, keyword_index = build(store, GatedEmbedder())
    removed = service.remove_orphans()
    check(not found_by_keyword(keyword_index, ghost), "after cleanup its text is no longer found by keyword")
    check(not any(leftovers(ghost).values()), "knowledge rows removed", str(removed))
    check(store.of(ghost) == 0, "vector points removed", str(removed))
    check(leftovers(kept)["chunks"] > 0, "a live document's rows survive cleanup")
    service._executor.shutdown(wait=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        engine.dispose()
        shutil.rmtree(WORKDIR, ignore_errors=True)
    sys.exit(code)
