"""Integration test for the hybrid retrieval stack against a temporary database.

Uses a stub vector store and embedder so the keyword, entity and expansion paths are
exercised without Ollama or Qdrant. Run: python tests/test_retrieval_integration.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_TMP = Path(tempfile.mkdtemp(prefix="rag-retrieval-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")

from app.core.retrieval import EvidenceTier  # noqa: E402
from app.models.database import init_database  # noqa: E402
from app.parsers.markdown_parser import MarkdownParser  # noqa: E402
from app.services.chunking import HeadingAwareChunker  # noqa: E402
from app.services.context_builder import ContextBuilder  # noqa: E402
from app.services.entities import EntityExtractor  # noqa: E402
from app.services.keyword_index import KeywordIndex  # noqa: E402
from app.services.knowledge_store import KnowledgeStore  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.reranking import FeatureReranker  # noqa: E402
from app.services.retriever import HybridRetriever  # noqa: E402
from app.services.structure import DocumentStructureAnalyzer  # noqa: E402
from app.services.summaries import SectionSummarizer  # noqa: E402

FAILURES: list[str] = []

DOC = """---
title: ملف القضية
version: 2.0
---

# ملف القضية

## 1. بيانات العقد

### أ) العقد

| البند | القيمة |
|---|---|
| **رقم العقد المعتمد** | B1N-2024-005221-P01 |
| **تاريخ توقيع العقد** | 09/10/2024 |
| **قيمة العقد الإجمالية** | 2,680,000 درهم |

### ب) رخصة البناء

| البند | القيمة |
|---|---|
| **رقم رخصة البناء** | B1N-2024-005221-P01 |
| **مصدر الرخصة** | بلدية مدينة أبوظبي |

### ج) مخطط الموقع

| البند | القيمة |
|---|---|
| **رقم المخطط** | A10 |
| **الاستشاري المُصدر** | Future Build Engineering Consultancy |

## 2. الغرامة

### أ) المعادلة

الغرامة اليومية ≈ 1,985.19 درهم والسقف الأقصى 268,000 درهم.

### ب) السيناريوهات

| مدة التأخير | الغرامة |
|---|---|
| 27 يوم | 53,600 درهم |
| 90 يوم | 178,667 درهم |

## 3. مواضيع غير ذات صلة

هذا القسم يتحدث عن ترتيبات السفر واللوجستيات ولا علاقة له بالعقد أو الغرامة.
"""


class StubVectorStore:
    """Returns nothing, so the test measures the keyword/entity/expansion paths alone."""

    def search(self, **_kwargs) -> list[dict]:
        return []


class StubEmbedder:
    def embed_one(self, _text: str) -> list[float]:
        return [0.0] * 8


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")


def build() -> HybridRetriever:
    init_database()

    parsed = MarkdownParser().parse(DOC.encode("utf-8"), "case.md")
    DocumentStructureAnalyzer().analyze(parsed)
    SectionSummarizer().apply(parsed.sections)
    entities = EntityExtractor().extract(parsed.sections)
    chunks = HeadingAwareChunker(900, 150, 150).chunk(parsed, "doc-1", "case.md")

    knowledge = KnowledgeStore()
    knowledge.replace_document(
        document_id="doc-1", sections=parsed.sections, entities=entities,
        chunks=chunks, category="Legal", version="2.0", language="ar",
    )

    keyword_index = KeywordIndex()
    keyword_index.rebuild()

    print(f"    indexed: {len(parsed.sections)} sections, {len(entities)} entities, "
          f"{len(chunks)} chunks, {keyword_index.stats()['terms']} terms")
    for chunk in chunks:
        print(f"      chunk {chunk.index} [{chunk.section_id}] {chunk.section[:60]}")

    return HybridRetriever(
        embedder=StubEmbedder(), store=StubVectorStore(), keyword_index=keyword_index,
        knowledge=knowledge, reranker=FeatureReranker(), top_k=5, candidate_pool=20,
        score_threshold=0.35, vector_score_floor=0.25, min_rerank_score=0.05,
        max_expanded_candidates=40, wide_top_k=10,
    )


def main() -> None:
    analyzer = QueryAnalyzer()
    retriever = build()

    print("\n-- exact identifier (the case vector search used to miss) --")
    analysis = analyzer.analyze("ما رقم العقد B1N-2024-005221-P01؟")
    results = retriever.retrieve(analysis)
    check(bool(results), "identifier query returns candidates without any vector hit")
    check(
        any("B1N-2024-005221-P01" in c.content for c in results),
        "the chunk holding the identifier is retrieved",
    )

    print("\n-- label wording differs from the question --")
    analysis = analyzer.analyze("كم قيمة العقد الإجمالية؟")
    results = retriever.retrieve(analysis)
    check(any("2,680,000" in c.content for c in results), "amount found via keyword/entity path")

    print("\n-- multi-section question widens retrieval --")
    narrow = retriever.retrieve(analyzer.analyze("كم قيمة العقد الإجمالية؟"))
    wide_analysis = analyzer.analyze("ما رقم العقد، وما رقم رخصة البناء، ومن الاستشاري المصدر للمخطط؟")
    wide = retriever.retrieve(wide_analysis)
    check(wide_analysis.wants_wide_retrieval, "multi-part question flagged for wide retrieval")
    check(len(wide) >= len(narrow), "wide retrieval returns at least as many candidates")
    wide_text = " ".join(c.content for c in wide)
    check("B1N-2024-005221-P01" in wide_text, "contract/licence number present in evidence")
    check("A10" in wide_text, "site layout number present in evidence")
    check("Future Build" in wide_text, "consultant name present in evidence")

    print("\n-- expansion reaches sibling sections --")
    analysis = analyzer.analyze("اذكر جميع سيناريوهات الغرامة والمعادلة المعتمدة")
    results = retriever.retrieve(analysis)
    joined = " ".join(c.content for c in results)
    check("1,985.19" in joined, "formula section retrieved")
    check("178,667" in joined, "scenarios section retrieved alongside it")
    check(
        any("expansion" in c.origins or "gap-fill" in c.origins for c in results)
        or len({c.section_id for c in results}) >= 2,
        "related sections pulled in",
    )

    print("\n-- irrelevant question (no vector arm, so coverage must decide) --")
    results = retriever.retrieve(analyzer.analyze("ما هي سياسة العمل عن بعد وعدد أيام الإجازات السنوية؟"))
    check(results == [], "uncovered question returns nothing instead of weak evidence")

    covered = retriever.retrieve(analyzer.analyze("ما قيمة العقد الإجمالية؟"))
    check(bool(covered), "a covered question still passes the same gate")

    print("\n-- context assembly --")
    analysis = analyzer.analyze("ما رقم العقد وما قيمته؟")
    results = retriever.retrieve(analysis)
    context, sources = ContextBuilder(6000, 2).build(results)
    check(bool(context), "context built")
    check(all(s.citation > 0 for s in sources), "every source numbered")
    check(all(s.section_path for s in sources), "every source carries a section path")
    check(
        any(s.tier == EvidenceTier.PRIMARY for s in sources),
        "at least one primary evidence block",
    )
    check(
        context.count("] الملف:") == len(sources),
        "context block count matches citation count",
    )

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("All retrieval integration checks passed.")


if __name__ == "__main__":
    main()
