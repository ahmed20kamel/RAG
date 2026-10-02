"""A project folder uploaded as it is: every file keeps its project and its folder.

A document used to be identified by its filename alone, so a second "Letter 01.pdf"
replaced the first — harmless one file at a time, and destructive for a project folder,
where Letters, Payments and Variations each have their own numbered files and two
projects share every file name there is. This checks that a file is the same document
only in the same place, and that the library can be read one project at a time.

Offline: a temporary database and upload folder, an in-memory vector store, a fixed
embedder.

Run: python tests/test_folder_upload.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-folder-upload-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(WORKDIR / "uploads")
(WORKDIR / "uploads").mkdir()

from app.config import get_settings  # noqa: E402
from app.models.database import engine, init_database, session_scope  # noqa: E402
from app.parsers.base import ParserRegistry  # noqa: E402
from app.parsers.markdown_parser import MarkdownParser  # noqa: E402
from app.schemas.document import DocumentMetadataInput, DocumentResponse  # noqa: E402
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
    def __init__(self) -> None:
        self.points: dict[str, str] = {}

    def ensure_collection(self, dimension: int) -> None:
        pass

    def upsert_chunks(self, chunks, vectors, payload_extra) -> int:
        for chunk in chunks:
            self.points[chunk.chunk_id] = chunk.document_id
        return len(chunks)

    def delete_document(self, document_id: str) -> None:
        for key in [k for k, v in self.points.items() if v == document_id]:
            del self.points[key]

    def document_ids(self) -> set[str]:
        return set(self.points.values())


class FixedEmbedder:
    dimension = 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def build() -> DocumentService:
    settings = get_settings()
    registry = ParserRegistry([MarkdownParser(settings.max_heading_depth)])
    knowledge, keyword_index, store = KnowledgeStore(), KeywordIndex(), MemoryStore()
    pipeline = IngestionPipeline(
        registry=registry,
        chunker=HeadingAwareChunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap,
                                    min_chunk_size=settings.min_chunk_size),
        embedder=FixedEmbedder(), store=store, structure=DocumentStructureAnalyzer(),
        entity_extractor=EntityExtractor(), summarizer=SectionSummarizer(),
        knowledge=knowledge, keyword_index=keyword_index,
    )
    return DocumentService(settings=settings, pipeline=pipeline, registry=registry,
                           store=store, knowledge=knowledge, keyword_index=keyword_index)


def body(text: str) -> bytes:
    return f"# Letter\n\n## Subject\n\n{text} — the contractor is requested to proceed with the works.\n".encode()


def upload(service, name, text, **meta) -> DocumentResponse:
    with session_scope() as session:
        return DocumentResponse.model_validate(
            service.upload(session=session, filename=name, content=body(text), metadata=DocumentMetadataInput(**meta))
        )


def main() -> int:
    init_database()
    service = build()

    print("\n=== 1. the same file name in different places is a different document ===")
    a = upload(service, "Letter 01.pdf.md", "Villa A letter one", project="Villa A", folder="Letters", category="Letters")
    b = upload(service, "Letter 01.pdf.md", "Villa A payment one", project="Villa A", folder="Payments", category="Payments")
    c = upload(service, "Letter 01.pdf.md", "Villa B letter one", project="Villa B", folder="Letters", category="Letters")
    check(len({a.id, b.id, c.id}) == 3, "three folders, three documents", f"{a.id[:8]} {b.id[:8]} {c.id[:8]}")
    check((a.project, a.folder, a.category) == ("Villa A", "Letters", "Letters"), "project, folder and type are kept")

    print("\n=== 2. the same file in the same place is a new version of it ===")
    a2 = upload(service, "Letter 01.pdf.md", "Villa A letter one, revised", project="Villa A", folder="Letters", category="Letters")
    check(a2.id == a.id, "re-uploaded in place: replaces, not duplicates")
    plain1 = upload(service, "note.md", "a single note")
    plain2 = upload(service, "note.md", "a single note, edited")
    check(plain1.id == plain2.id and plain1.project == "", "a single-file upload behaves as before")

    print("\n=== 3. the library read one project at a time ===")
    with session_scope() as session:
        total_a, items_a = service.list_documents(session, project="Villa A")
        total_all, _ = service.list_documents(session)
        projects = service.projects(session)
    check(total_a == 2 and {d.folder for d in items_a} == {"Letters", "Payments"}, "Villa A has its two documents", str(total_a))
    check(total_all == 4, "the whole library still lists everything", str(total_all))
    check(projects == ["Villa A", "Villa B"], "projects are listed once each", str(projects))

    print("\n=== 4. a folder name that arrives with stray slashes ===")
    d = upload(service, "Spec.md", "spec text", project="Villa A", folder="/Engineering/Specs/", category="Engineering")
    check(d.folder == "Engineering/Specs", "slashes at either end are dropped", d.folder)

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
