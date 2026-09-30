"""Offline checks for document intelligence, hybrid retrieval parts and validation.

No Ollama, Qdrant or database required.
Run: python tests/test_intelligence.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.domain import DocumentType  # noqa: E402
from app.core.retrieval import Candidate, EvidenceTier  # noqa: E402
from app.core.text import (  # noqa: E402
    detect_language,
    normalize,
    numeric_tokens,
    stem,
    tokenize,
)
from app.parsers.markdown_parser import MarkdownParser  # noqa: E402
from app.services.answer_validation import AnswerValidator  # noqa: E402
from app.services.chunking import HeadingAwareChunker  # noqa: E402
from app.services.context_builder import ContextBuilder  # noqa: E402
from app.services.entities import EntityExtractor  # noqa: E402
from app.services.keyword_index import KeywordIndex  # noqa: E402
from app.services.query_analysis import Intent, QueryAnalyzer  # noqa: E402
from app.services.reranking import FeatureReranker  # noqa: E402
from app.services.structure import DocumentStructureAnalyzer  # noqa: E402
from app.services.summaries import SectionSummarizer  # noqa: E402

FAILURES: list[str] = []

SAMPLE = """---
title: ملف القضية
category: Legal
version: 2.0
---

# ملف القضية

## 1. الأطراف

### أ) أطراف القضية

| البند | التفاصيل |
|---|---|
| **المدعي** | شركة الشركة للنقليات والمقاولات العامة ذ.م.م |
| **الرخصة التجارية للالشركة** | CN-1028096 |
| **المدعى عليه** | شركة الأفق للمقاولات العامة ذ.م.م |
| **ممثل الأفق القانوني** | السيد/ وليد منير الكعبي |

### ب) العقد

| البند | القيمة |
|---|---|
| **رقم العقد المعتمد** | B1N-2023-004410-P01 |
| **تاريخ توقيع العقد** | 21/07/2024 |
| **قيمة العقد الإجمالية** | 1,450,000 درهم |
| **مدة المشروع** | 540 يوماً |

## 2. الغرامة

نصت المحكمة في جلسة 24/04/2026 على ندب خبير هندسي.
الغرامة اليومية ≈ 1,450.00 درهم بحد أقصى 50,000 درهم وفق البند 10.
"""


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")


def build_document():
    parsed = MarkdownParser().parse(SAMPLE.encode("utf-8"), "case.md")
    DocumentStructureAnalyzer().analyze(parsed)
    SectionSummarizer().apply(parsed.sections)
    return parsed


def test_text() -> None:
    print("\n-- text normalisation --")
    check(normalize("الإجراءات") == normalize("الاجراءات"), "alef variants fold together")
    check(normalize("مدة") == normalize("مده"), "ta-marbuta folds to ha")
    check(normalize("١٩٨٥") == "1985", "Arabic-Indic digits convert")

    tokens = tokenize("ما رقم العقد B1N-2023-004410-P01 ؟")
    check("b1n-2023-004410-p01" in tokens, "identifier kept whole")
    check("2023" in tokens, "identifier also split into parts")
    check("ما" not in tokens, "stopwords dropped")

    # Arabic punctuation lives inside the Arabic Unicode block, so a naive \w class
    # keeps it glued to the word and produces terms that can never match.
    check(
        tokenize("ما قيمة العقد الإجمالية؟") == tokenize("ما قيمة العقد الإجمالية"),
        "trailing Arabic question mark changes nothing",
    )
    check(
        not any("؟" in t or "،" in t for t in tokenize("ما رقم العقد، وما قيمته؟")),
        "no token keeps Arabic punctuation",
    )

    check(stem(normalize("السيناريوهات")) == stem(normalize("سيناريوهات")),
          "definite article does not block a match")
    check(stem(normalize("للمقاولات")) == stem(normalize("المقاول")), "prefix+suffix stemmed")
    check(stem(normalize("وليد")) == normalize("وليد"), "short names are left intact")

    check(detect_language("ما هو العقد") == "ar", "Arabic detected")
    check(detect_language("what is the contract") == "en", "English detected")
    check("50,000" in numeric_tokens("بحد أقصى 50,000 درهم"), "numeric token captured")


def test_structure() -> None:
    print("\n-- structure analysis --")
    parsed = build_document()
    by_heading = {s.heading: s for s in parsed.sections}

    parties = by_heading["أ) أطراف القضية"]
    parent = next(s for s in parsed.sections if s.section_id == parties.parent_id)
    check(parent.heading == "1. الأطراف", "parent link points at the enclosing section")
    check(parties.section_id in parent.child_ids, "child link recorded on the parent")
    check(parties.has_table, "table detected in section")
    check(all(s.section_id for s in parsed.sections), "every section got an id")
    check(parsed.document_type == DocumentType.LEGAL_CASE, "document type detected as legal case")

    contract = by_heading["ب) العقد"]
    check(contract.parent_id == parties.parent_id, "siblings share a parent")


def test_entities() -> None:
    print("\n-- entity extraction --")
    parsed = build_document()
    entities = EntityExtractor().extract(parsed.sections)
    values = {e.value for e in entities}
    labels = {e.label for e in entities if e.label}
    kinds = {e.kind for e in entities}

    check(any("B1N-2023-004410-P01" in v for v in values), "contract identifier extracted")
    check(any("CN-1028096" in v for v in values), "licence identifier extracted")
    check("21/07/2024" in values, "date extracted")
    check(any("1,450,000" in v for v in values), "amount extracted")
    check("رقم العقد المعتمد" in labels, "table label captured with its value")
    check({"date", "identifier", "amount"} <= kinds, "expected entity kinds present")
    check(any("البند 10" in v for v in values), "clause reference extracted")

    source = normalize(SAMPLE)
    invented = [e.value for e in entities if normalize(e.value) not in source]
    check(not invented, f"no entity invented (found {invented[:3]})")


def test_summaries() -> None:
    print("\n-- extractive summaries --")
    parsed = build_document()
    contract = next(s for s in parsed.sections if s.heading == "ب) العقد")
    check(bool(contract.summary), "summary produced")
    check("رقم العقد المعتمد" in contract.summary, "summary lists the table labels")
    check(len(contract.summary) <= 400, "summary stays short")

    narrative = next(s for s in parsed.sections if s.heading == "2. الغرامة")
    source = normalize(SAMPLE)
    body = narrative.summary.split("—", 1)[-1]
    check(
        all(
            normalize(part.strip()) in source
            for part in body.split("|")
            if part.strip() and not part.strip().startswith("يغطي")
        ),
        "narrative summary is verbatim from the section",
    )


def test_keyword_index() -> None:
    print("\n-- BM25 keyword index --")
    parsed = build_document()
    chunks = HeadingAwareChunker(1400, 200, 200).chunk(parsed, "doc-1", "case.md")
    index = KeywordIndex()
    index.build_from([(c.chunk_id, "doc-1", f"{c.section}\n{c.content}") for c in chunks])

    results = index.search("B1N-2023-004410-P01")
    check(bool(results), "exact identifier is findable by keyword search")
    top_chunk = next(c for c in chunks if c.chunk_id == results[0][0])
    check("B1N-2023-004410-P01" in top_chunk.content, "top keyword hit holds the identifier")

    check(bool(index.search("وليد منير")), "person name findable")
    check(index.search("قطط وفضاء") == [], "unrelated query returns nothing")
    check(index.stats()["chunks"] == len(chunks), "index covers every chunk")


def test_query_analysis() -> None:
    print("\n-- query understanding --")
    analyzer = QueryAnalyzer()

    check(analyzer.analyze("كم قيمة العقد؟").intent == Intent.NUMERIC, "numeric intent")
    check(analyzer.analyze("متى وُقّع العقد؟").intent == Intent.DATE, "date intent")
    check(analyzer.analyze("من هم أطراف القضية؟").intent == Intent.ENTITY, "entity intent")
    check(analyzer.analyze("قارن بين السيناريوهات").intent == Intent.COMPARISON, "comparison intent")
    check(analyzer.analyze("اذكر تسلسل الأحداث").intent == Intent.TIMELINE, "timeline intent")

    wide = analyzer.analyze("من هم أطراف القضية؟")
    check(wide.wants_wide_retrieval, "entity questions widen retrieval")

    multi = analyzer.analyze("ما رقم العقد، وما تاريخ توقيعه؟")
    check(multi.multi_part, "multi-part question detected")

    ident = analyzer.analyze("ما تفاصيل العقد B1N-2023-004410-P01؟")
    check("b1n-2023-004410-p01" in [i.lower() for i in ident.identifiers], "identifier pulled from question")

    narrow = analyzer.analyze("ما قيمة العقد الإجمالية؟")
    check(not narrow.wants_wide_retrieval, "simple factual question stays narrow")


def make_candidate(chunk_id: str, content: str, section: str, **kwargs) -> Candidate:
    return Candidate(
        chunk_id=chunk_id, document_id="doc-1", filename="case.md", document_title="ملف القضية",
        section=section, section_id=chunk_id, heading=section, parent_section="",
        content=content, **kwargs,
    )


def test_reranker() -> None:
    print("\n-- reranking --")
    analysis = QueryAnalyzer().analyze("ما رقم العقد B1N-2023-004410-P01؟")
    weak = make_candidate("c1", "نص عام لا يحتوي على شيء مفيد هنا", "قسم عام", fused_score=1.0)
    strong = make_candidate("c2", "رقم العقد المعتمد B1N-2023-004410-P01 بتاريخ 21/07/2024", "العقد", fused_score=0.5)

    ranked = FeatureReranker().rerank(analysis, [weak, strong], limit=2)
    check(ranked[0].chunk_id == "c2", "identifier match outranks a higher fused score")
    check(ranked[0].rerank_score > ranked[1].rerank_score, "scores are ordered")


def test_context_tiers() -> None:
    print("\n-- tiered context --")
    primary = make_candidate("c1", "قيمة العقد 1,450,000 درهم", "العقد", rerank_score=3.0)
    primary.origins.add("vector")
    support = make_candidate("c2", "مدة المشروع 540 يوماً", "العقد", rerank_score=2.0)
    support.origins.add("keyword")
    related = make_candidate("c3", "تفاصيل إضافية عن الجلسات", "الجلسات", rerank_score=1.0)
    related.origins.add("expansion")

    context, sources = ContextBuilder(5000, primary_count=1).build([primary, support, related])
    check("[الأدلة الأساسية]" in context, "primary tier labelled in context")
    check("[أقسام ذات صلة]" in context, "related tier labelled in context")
    check([s.citation for s in sources] == [1, 2, 3], "citations numbered in order")
    check(sources[0].tier == EvidenceTier.PRIMARY, "top evidence marked primary")
    check(sources[2].tier == EvidenceTier.RELATED, "expansion-only evidence marked related")
    check(all(s.section_path for s in sources), "every citation carries a section path")


def test_validation() -> None:
    print("\n-- answer validation --")
    validator = AnswerValidator()
    analyzer = QueryAnalyzer()
    context = "[1] الملف: case.md | القسم: العقد\nقيمة العقد 1,450,000 درهم ومدته 540 يوماً."
    sources = ContextBuilder(5000, 1).build(
        [make_candidate("c1", "قيمة العقد 1,450,000 درهم ومدته 540 يوماً", "العقد", rerank_score=3.0)]
    )[1]

    good = validator.validate(
        analyzer.analyze("ما قيمة العقد؟"), "قيمة العقد 1,450,000 درهم [1].", context, sources
    )
    check(not good.unsupported_values, "supported values pass")
    check(good.cited_sources == [1], "citation detected")

    bad = validator.validate(
        analyzer.analyze("ما قيمة العقد؟"), "قيمة العقد 9,999,999 درهم [1].", context, sources
    )
    check(bool(bad.unsupported_values), "invented number flagged")
    check(not bad.complete, "answer with invented value is not complete")

    uncited = validator.validate(
        analyzer.analyze("ما قيمة العقد؟"), "قيمة العقد 1,450,000 درهم.", context, sources
    )
    check(bool(uncited.warnings), "missing citation raises a warning")

    partial = validator.validate(
        analyzer.analyze("ما رقم العقد، وما تاريخ توقيعه؟"),
        "رقم العقد B1N [1].", context, sources,
    )
    check(bool(partial.unanswered_parts), "unanswered part of a multi-part question flagged")


if __name__ == "__main__":
    test_text()
    test_structure()
    test_entities()
    test_summaries()
    test_keyword_index()
    test_query_analysis()
    test_reranker()
    test_context_tiers()
    test_validation()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("All intelligence checks passed.")
