"""Each person sees, opens and is answered from their own documents only.

Before owners, every signed-in person saw every document, and an upload could collide
with someone else's in two ways: a file of the same name replaced theirs, and a file of
the same content was refused with the other person's filename in the message. This checks
the library, a document's pages, the evidence a question may use and the name resolver,
for two people and an administrator — and the one trap in the filter: a person with no
documents must search nothing, not everything.

Offline: a temporary database and upload folder, an in-memory vector store.

Run: python tests/test_document_access.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-access-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(WORKDIR / "uploads")
(WORKDIR / "uploads").mkdir()

from app.api.routes.documents import _visible  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.permissions import Permission, has_permission  # noqa: E402
from app.exceptions import DocumentNotFoundError  # noqa: E402
from app.models.database import engine, init_database, session_scope  # noqa: E402
from app.parsers.base import ParserRegistry  # noqa: E402
from app.parsers.markdown_parser import MarkdownParser  # noqa: E402
from app.schemas.document import DocumentMetadataInput, DocumentResponse  # noqa: E402
from app.services import access  # noqa: E402
from app.services.chunking import HeadingAwareChunker  # noqa: E402
from app.services.document_scope import DocumentScope, known  # noqa: E402
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


def build() -> tuple[DocumentService, KeywordIndex]:
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
    service = DocumentService(settings=settings, pipeline=pipeline, registry=registry,
                              store=store, knowledge=knowledge, keyword_index=keyword_index)
    return service, keyword_index


ALICE = SimpleNamespace(id="u-alice", role="contributor", email="alice@example.test")
BOB = SimpleNamespace(id="u-bob", role="contributor", email="bob@example.test")
NEWCOMER = SimpleNamespace(id="u-new", role="contributor", email="new@example.test")
ADMIN = SimpleNamespace(id="u-admin", role="admin", email="admin@example.test")
SERVICE = SimpleNamespace(id="u-svc", role="service", email="svc@example.test")

CONTRACT = "# Supply contract\n\n## Payment\n\nThe advance payment is 125,000 dirhams, due on signature of the contract.\n"


def upload(service, name, text, owner) -> DocumentResponse:
    with session_scope() as session:
        return DocumentResponse.model_validate(service.upload(
            session=session, filename=name, content=text.encode(),
            metadata=DocumentMetadataInput(), owner_id=owner.id if owner else None,
        ))


def retriever_leaks(mine: str, theirs: str) -> tuple[bool, bool]:
    """Found live: a reader whose only file was one short image asked about payment
    terms, the expansion step searched every document for the words her file lacked,
    and the answer quoted another person's contract. Both halves are checked: the
    search is confined, and anything outside the filter is dropped whatever added it."""
    from app.core.retrieval import Candidate
    from app.services.retriever import HybridRetriever

    def chunk(chunk_id: str, document_id: str) -> Candidate:
        return Candidate(chunk_id=chunk_id, document_id=document_id, filename=document_id,
                         document_title="", section="s", section_id="s", heading="",
                         parent_section="", content="payment terms", vector_score=0.9)

    searched_with: list = []
    record = SimpleNamespace(**{f: "" for f in ("filename", "document_title", "section", "section_id",
                                                 "heading", "parent_section", "content", "version",
                                                 "category", "language", "locator")},
                             chunk_id="theirs-1", document_id=theirs, has_table=False, page=None)

    def search(query, limit=20, document_ids=None, **_):
        searched_with.append(document_ids)
        return [] if document_ids and theirs not in document_ids else [("theirs-1", 3.0)]

    r = HybridRetriever.__new__(HybridRetriever)
    r.max_expanded_candidates = 40
    r.knowledge = SimpleNamespace(chunks_in_sections=lambda *a, **k: [],
                                  chunks_by_ids=lambda ids: {"theirs-1": record})
    r.keyword_index = SimpleNamespace(search=search)
    r._uncovered_terms = lambda analysis, selected: {"terms"}
    candidates = {"mine-1": chunk("mine-1", mine)}
    analysis = SimpleNamespace(wants_wide_retrieval=False)
    r._expand(analysis, list(candidates.values()), candidates, 8, [mine])
    gap_fill_leak = "theirs-1" in candidates or searched_with != [[mine]]

    # Any arm at all: here the vector arm is made to ignore the filter.
    r.top_k = r.wide_top_k = 8
    r.min_rerank_score = -1.0
    r.enable_keyword_search = r.enable_entity_retrieval = r.enable_expansion = False
    r._add_vector_hits = lambda analysis, pool, category, ids: pool.update(
        {"mine-1": chunk("mine-1", mine), "theirs-2": chunk("theirs-2", theirs)})
    r._has_coverage = lambda analysis, pool: True
    r._reserve_semantic_slots = lambda analysis, pool, final, limit: final
    r.reranker = SimpleNamespace(rerank=lambda analysis, items, limit: [
        c for c in items if setattr(c, "rerank_score", 1.0) is None][:limit])
    full = SimpleNamespace(wants_wide_retrieval=False, needs_dated_sweep=False, question="q",
                           intent=None, temporal=SimpleNamespace(is_temporal=False))
    final = r.retrieve(full, document_ids=[mine])
    return gap_fill_leak, any(c.document_id != mine for c in final)


def main() -> int:
    init_database()
    service, keyword_index = build()

    print("\n=== 1. the same file from two people is two documents ===")
    a = upload(service, "contract.md", CONTRACT, ALICE)
    b = upload(service, "contract.md", CONTRACT, BOB)
    legacy = upload(service, "legacy.md", "# Old matter\n\n## Note\n\nA document from before owners existed, 990,000.\n", None)
    check(a.id != b.id, "same name and content: Bob's upload does not replace Alice's")
    with session_scope() as session:
        owners = {d.id: d.owner_id for d in service.list_documents(session)[1]}
    check(owners.get(a.id) == ALICE.id and owners.get(b.id) == BOB.id, "each document records who uploaded it")
    a2 = upload(service, "contract.md", CONTRACT.replace("125,000", "130,000"), ALICE)
    check(a2.id == a.id, "Alice re-uploading her own file still replaces her own copy")
    service._executor.shutdown(wait=True)
    keyword_index.rebuild()

    print("\n=== 2. the library, per reader ===")
    with session_scope() as session:
        alice_total, alice_items = service.list_documents(session, owner_id=access.owner_filter(ALICE))
        admin_total, _ = service.list_documents(session, owner_id=access.owner_filter(ADMIN))
        new_total, _ = service.list_documents(session, owner_id=access.owner_filter(NEWCOMER))
        alice_stats = service.library_stats(session, access.owner_filter(ALICE))
    check(alice_total == 1 and alice_items[0].id == a.id, "Alice lists only her document", str(alice_total))
    check(admin_total == 3, "the administrator lists every document, including the ownerless one", str(admin_total))
    check(new_total == 0, "someone who uploaded nothing lists nothing")
    check(alice_stats.documents == 1, "Alice's totals count only her documents", str(alice_stats.documents))

    print("\n=== 3. opening a document ===")
    with session_scope() as session:
        try:
            _visible(session, service, ALICE, b.id)
            check(False, "Alice cannot open Bob's document")
        except DocumentNotFoundError:
            check(True, "Alice cannot open Bob's document (reads as not found)")
        check(_visible(session, service, ALICE, a.id).id == a.id, "Alice can open her own")
        check(_visible(session, service, ADMIN, b.id).id == b.id, "the administrator can open Bob's")
        try:
            _visible(session, service, BOB, legacy.id)
            check(False, "an ownerless document is not open to an ordinary user")
        except DocumentNotFoundError:
            check(True, "an ownerless document is not open to an ordinary user")

    print("\n=== 4. the evidence a question may use ===")
    check(access.visible_ids(ADMIN) is None and access.visible_ids(SERVICE) is None,
          "administrator and integration are not restricted")
    check(access.visible_ids(ALICE) == [a.id], "Alice may be answered from her document only")
    check(access.restrict(None, access.visible_ids(ALICE)) == [a.id], "an unscoped question searches Alice's documents")
    check(access.restrict([b.id], access.visible_ids(ALICE)) == [access.NO_DOCUMENT],
          "asking for Bob's document by id searches nothing")
    check(access.restrict(None, access.visible_ids(NEWCOMER)) == [access.NO_DOCUMENT],
          "no documents: the filter matches nothing — never an empty filter meaning everything")
    hits_all = keyword_index.search("advance payment dirhams", limit=10)
    hits_none = keyword_index.search("advance payment dirhams", limit=10, document_ids=[access.NO_DOCUMENT])
    check(bool(hits_all) and not hits_none, "the no-document filter really returns nothing from search")

    print("\n=== 5. a file named in the question ===")
    scope = DocumentScope(loader=lambda: [known(a.id, "contract.md"), known(b.id, "contract.md"),
                                          known(legacy.id, "legacy.md")])
    shared = scope.resolve("what is the advance in contract.md")
    check(shared.ambiguous, "for the administrator two files share the name: a question back")
    mine = scope.resolve("what is the advance in contract.md", among=[a.id])
    check(not mine.ambiguous and mine.document is not None and mine.document.document_id == a.id,
          "for Alice the name means her file — Bob's does not make it ambiguous")
    hidden = scope.resolve("what is in legacy.md", among=[a.id])
    check(hidden.document is None, "a name only someone else's file carries is not a match")

    print("\n=== 6. deleting ===")
    check(not has_permission("contributor", Permission.DOCUMENT_DELETE), "a contributor cannot delete others' documents")
    check(has_permission("admin", Permission.DOCUMENT_DELETE), "the administrator can")
    check(has_permission("admin", Permission.DOCUMENT_READ_ALL) and not has_permission("contributor", Permission.DOCUMENT_READ_ALL),
          "only the administrator (and integrations) read every document")

    print("\n=== 7. no search step reaches past the reader's documents ===")
    gap_fill_leak, leaked_by_arm = retriever_leaks(a.id, b.id)
    check(not gap_fill_leak, "the gap-filling keyword search keeps to the reader's documents",
          "it searched every document — a small file let someone else's passages in")
    check(not leaked_by_arm, "a passage another arm brought in from outside the filter is dropped")

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
