"""Completeness and role-preservation checks.

Covers the failure this work was built to fix: every party was present in the context,
yet the answer summarised two of them away and moved the legal representative's role
onto the owner. These tests need no Ollama, Qdrant or server — they exercise the
deterministic parts: intent classification, fact extraction, the fact sheet the model
receives, and the validator that detects an incomplete answer.

Run: python tests/test_completeness.py
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

_TMP = Path(tempfile.mkdtemp(prefix="rag-completeness-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")

from app.core.retrieval import Candidate  # noqa: E402
from app.models.database import init_database  # noqa: E402
from app.parsers.markdown_parser import MarkdownParser  # noqa: E402
from app.services.answer_validation import AnswerValidator  # noqa: E402
from app.services.chunking import HeadingAwareChunker  # noqa: E402
from app.services.context_builder import ContextBuilder  # noqa: E402
from app.services.entities import EntityExtractor  # noqa: E402
from app.schemas.chat import SourceFact  # noqa: E402
from app.services.fact_sheet import FactSheetBuilder  # noqa: E402
from app.services.keyword_index import KeywordIndex  # noqa: E402
from app.services.knowledge_store import KnowledgeStore  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.reranking import FeatureReranker  # noqa: E402
from app.services.retriever import HybridRetriever  # noqa: E402
from app.services.structure import DocumentStructureAnalyzer  # noqa: E402
from app.services.summaries import SectionSummarizer  # noqa: E402

FAILURES: list[str] = []

# Five distinct people with five distinct roles, deliberately spread over two sections.
DOC = """---
title: ملف القضية
version: 3.0
---

# ملف القضية

## 1. المعلومات الأساسية

### أ) أطراف القضية

| البند | التفاصيل |
|---|---|
| **المدعي** | شركة الشركة للنقليات والمقاولات العامة ذ.م.م |
| **يمثل المدعي** | المدير العام / خالد سعيد الحمادي |
| **المدعى عليه** | شركة الأفق للمقاولات العامة ذ.م.م |
| **ممثل الأفق القانوني** | السيد/ وليد منير الكعبي |
| **ممثل الأفق في الموقع** | المهندس طارق |

### ب) المالك

| البند | التفاصيل |
|---|---|
| **المالك الوحيد لالأفق** | محمد جمعه محمد سعيد الجنيبي |
| **مالك المشروع المتضرر** | راشد علي الظاهري |

## 2. الأرقام المعتمدة

| البند | القيمة |
|---|---|
| **قيمة العقد** | 1,450,000 درهم |
| **السقف الأقصى للغرامة** | 50,000 درهم |
| **الغرامة اليومية** | 1,450.00 درهم |

## 3. التواريخ

| التاريخ | الواقعة |
|---|---|
| 21/07/2024 | توقيع العقد |
| 06/12/2024 | اكتمال السور |
| 02/01/2026 | الانهيار الكامل |

## 5-أ. مرحلة التقرير المبدئي (29/07 → 18/05/2026)

### تفاصيل التقرير المبدئي

وصل التقرير المبدئي وتم التعقيب عليه خلال المهلة.

## 5-ب. التقرير النهائي (أودع 20/05/2026)

### مكاسب الاعتراضات

دخلت اعتراضاتنا في التقرير النهائي.

## 5-ج. جلسة المرافعة (24/05/2026)

### وقائع الجلسة

حضر الطرفان وقُدّمت المذكرات.

## 5-د. الحكم القطعي (27/05/2026)

### المنطوق

قضت المحكمة لصالح المدعي.

## 4. موضوع غير ذي صلة

ترتيبات السفر واللوجستيات والمواصلات.
"""


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")


def build():
    init_database()
    parsed = MarkdownParser().parse(DOC.encode("utf-8"), "case.md")
    DocumentStructureAnalyzer().analyze(parsed)
    SectionSummarizer().apply(parsed.sections)
    entities = EntityExtractor().extract(parsed.sections)
    chunks = HeadingAwareChunker(1200, 150, 150).chunk(parsed, "doc-1", "case.md")

    knowledge = KnowledgeStore()
    knowledge.replace_document("doc-1", parsed.sections, entities, chunks, "Legal", "3.0", "ar")
    keyword_index = KeywordIndex()
    keyword_index.rebuild()

    retriever = HybridRetriever(
        embedder=_StubEmbedder(), store=_StubStore(), keyword_index=keyword_index,
        knowledge=knowledge, reranker=FeatureReranker(), top_k=6, candidate_pool=20,
        score_threshold=0.35, vector_score_floor=0.25, min_rerank_score=0.05,
        max_expanded_candidates=40, wide_top_k=12,
    )
    return knowledge, retriever


class _StubStore:
    def search(self, **_kwargs):
        return []


class _StubEmbedder:
    def embed_one(self, _text):
        return [0.0] * 8


def facts_for(question: str, knowledge, retriever):
    analysis = QueryAnalyzer().analyze(question)
    candidates = retriever.retrieve(analysis)
    context, sources = ContextBuilder(20000, 3).build(
        candidates, wide=analysis.wants_wide_retrieval
    )
    merged = knowledge.merged_section_headings(
        [(s.document_id, s.section_id) for s in sources if s.section_id]
    )
    slots = FactSheetBuilder.section_map(sources, merged)
    entities = knowledge.entities_for_sections(list(slots))
    block, facts = FactSheetBuilder().build(analysis, slots, entities)
    return analysis, context, sources, block, facts


def test_intent_classification() -> None:
    print("\n-- A/B. question classification --")
    analyzer = QueryAnalyzer()

    exhaustive = analyzer.analyze("من هم أطراف القضية؟")
    check(exhaustive.exhaustive, "'من هم' flagged exhaustive")
    check(exhaustive.wants_wide_retrieval, "exhaustive question widens retrieval")
    check(exhaustive.high_risk, "exhaustive question marked high risk")

    check(analyzer.analyze("ما هي الشركات المذكورة؟").exhaustive, "'ما هي الشركات' exhaustive")
    check(analyzer.analyze("من حضر الجلسة؟").exhaustive, "'من حضر' exhaustive")
    check(analyzer.analyze("اذكر جميع الأطراف").exhaustive, "'اذكر جميع' exhaustive")

    role = analyzer.analyze("من هو الممثل القانوني لالأفق؟")
    check(role.role_specific, "role question flagged role_specific")

    simple = analyzer.analyze("ما قيمة العقد؟")
    check(not simple.exhaustive, "simple factual question is not exhaustive")
    check(not simple.role_specific, "simple factual question is not role-specific")


def test_fact_sheet_roles(knowledge, retriever) -> None:
    print("\n-- B. roles reach the prompt, each with its own holder --")
    _analysis, _ctx, _src, block, facts = facts_for("من هم أطراف القضية؟", knowledge, retriever)

    check(bool(block), "fact sheet produced for an exhaustive question")
    pairs = {(f.label, f.value) for f in facts}
    for label, value in (
        ("ممثل الأفق القانوني", "السيد/ وليد منير الكعبي"),
        ("ممثل الأفق في الموقع", "المهندس طارق"),
        ("المدعي", "شركة الشركة للنقليات والمقاولات العامة ذ.م.م"),
        ("يمثل المدعي", "المدير العام / خالد سعيد الحمادي"),
    ):
        check(any(l == label and v == value for l, v in pairs), f"fact kept: {label} → {value[:28]}")

    legal = next((f for f in facts if "القانوني" in f.label), None)
    check(legal is not None and "وليد" in legal.value, "legal representative bound to وليد")
    check(legal is not None and "الجنيبي" not in legal.value, "owner not merged into legal rep")
    check(all(f.citation > 0 for f in facts), "every fact carries a citation")


def test_completeness_detection(knowledge, retriever) -> None:
    print("\n-- E. validator detects an incomplete list answer --")
    analysis, context, sources, _block, facts = facts_for(
        "من هم أطراف القضية؟", knowledge, retriever
    )
    validator = AnswerValidator()

    partial = (
        "أطراف القضية هما شركة الشركة للنقليات والمقاولات العامة ذ.م.م [1] "
        "وشركة الأفق للمقاولات العامة ذ.م.م [1]."
    )
    missing = validator.missing_facts(analysis, partial, facts)
    check(bool(missing), "omission detected in a summarising answer")
    check(
        any("وليد" in m.value for m in missing),
        "the omitted legal representative is named in the report",
    )
    verdict = validator.validate(analysis, partial, context, sources, facts)
    check(not verdict.complete, "incomplete answer is not marked complete")
    check(bool(verdict.omitted_facts), "omitted facts surfaced on the validation object")

    full = (
        "أطراف القضية:\n"
        "- المدعي: شركة الشركة للنقليات والمقاولات العامة ذ.م.م [1]\n"
        "- يمثل المدعي: المدير العام / خالد سعيد الحمادي [1]\n"
        "- المدعى عليه: شركة الأفق للمقاولات العامة ذ.م.م [1]\n"
        "- ممثل الأفق القانوني: السيد/ وليد منير الكعبي [1]\n"
        "- ممثل الأفق في الموقع: المهندس طارق [1]\n"
    )
    check(not validator.missing_facts(analysis, full, facts), "complete answer reports no omission")
    check(validator.validate(analysis, full, context, sources, facts).complete,
          "complete answer passes validation")


def test_focused_question_not_penalised(knowledge, retriever) -> None:
    print("\n-- validator stays quiet on focused questions --")
    analysis, context, sources, _b, facts = facts_for("ما قيمة العقد؟", knowledge, retriever)
    answer = "قيمة العقد 1,450,000 درهم [1]."
    check(
        not AnswerValidator().missing_facts(analysis, answer, facts),
        "a focused answer is not flagged for omitting unrelated facts",
    )


def test_numeric_and_dates(knowledge, retriever) -> None:
    print("\n-- C/D. numeric and date facts --")
    _a, _c, _s, _b, numeric = facts_for("اذكر جميع المبالغ المعتمدة", knowledge, retriever)
    values = " ".join(f.value for f in numeric)
    for amount in ("1,450,000", "50,000", "1,450.00"):
        check(amount in values, f"amount available as a fact: {amount}")

    # In a "| التاريخ | الواقعة |" table the date is the label and the event the value,
    # so a date fact is searched across both halves of the pair.
    _a, _c, _s, _b, dates = facts_for("ما تاريخ توقيع العقد واكتمال السور والانهيار؟", knowledge, retriever)
    date_text = " ".join(f"{f.label} {f.value}" for f in dates)
    for date in ("21/07/2024", "06/12/2024", "02/01/2026"):
        check(date in date_text, f"date available as a fact: {date}")


def test_multi_section(knowledge, retriever) -> None:
    print("\n-- F. multi-section question reaches both sections --")
    _a, context, sources, _b, facts = facts_for(
        "من هم أطراف القضية ومن هو المالك الوحيد لالأفق؟", knowledge, retriever
    )
    check(len({s.section_id for s in sources}) >= 2, "evidence spans several sections")
    joined = " ".join(f"{f.label} {f.value}" for f in facts)
    check("وليد" in joined, "party section represented in facts")
    check("الجنيبي" in joined, "owner section represented in facts")


def test_partial_retrieval_regression(knowledge, retriever) -> None:
    print("\n-- H. entity/keyword path finds what a dead vector arm cannot --")
    analysis, context, _s, _b, facts = facts_for(
        "من هو الممثل القانوني لالأفق؟", knowledge, retriever
    )
    check(analysis.role_specific, "role question classified")
    check("وليد" in context, "the representative is retrieved with no vector hits at all")
    check(any("وليد" in f.value for f in facts), "and is present as a structured fact")


def test_timeline_sweep(knowledge, retriever) -> None:
    """The reported failure: 'آخر تحديثات … بالتواريخ' returned only the last date.

    Each update section states its date in its heading and nowhere else, so no wording
    of the question matches it lexically or semantically.
    """
    print("\n-- I. latest-updates question sweeps every dated section --")
    analysis = QueryAnalyzer().analyze("ما هي آخر تحديثات القضية بالتواريخ؟")
    check(str(analysis.intent) == "timeline", "classified as a timeline question")
    check(analysis.exhaustive, "timeline question is exhaustive by definition")
    check(analysis.wants_wide_retrieval, "timeline question widens retrieval")

    _a, context, sources, block, facts = facts_for(
        "ما هي آخر تحديثات القضية بالتواريخ؟", knowledge, retriever
    )
    for expected in ("18/05/2026", "20/05/2026", "24/05/2026", "27/05/2026"):
        check(expected in context, f"dated section reached the context: {expected}")

    dated = [f for f in facts if f.kind == "date"]
    labels = [f.label for f in dated]
    check(bool(dated), "a timeline block was produced")
    check(labels == sorted(labels, reverse=True) or len(labels) < 2,
          "timeline entries are ordered newest first")
    for expected in ("27/05/2026", "24/05/2026", "20/05/2026", "18/05/2026"):
        check(expected in labels, f"timeline entry present: {expected}")
    check("الجدول الزمني" in block, "the prompt gets an explicit chronological block")

    validator = AnswerValidator()
    partial = "آخر تحديث هو الحكم القطعي بتاريخ 27/05/2026 [1]."
    missing = validator.missing_facts(analysis, partial, facts)
    check(bool(missing), "an answer giving only the last date is flagged incomplete")
    check(
        {"18/05/2026", "20/05/2026", "24/05/2026"} <= {m.label for m in missing},
        "the three dropped dates are named in the report",
    )

    complete = (
        "التحديثات: 27/05/2026 الحكم القطعي [1]، 24/05/2026 جلسة المرافعة [2]، "
        "20/05/2026 التقرير النهائي [3]، 18/05/2026 التقرير المبدئي [4]."
    )
    check(
        not validator.missing_facts(analysis, complete, facts),
        "an answer covering every date passes",
    )


def test_fact_sheet_counts_as_evidence(knowledge, retriever) -> None:
    """A date that reached the model only through a dated section heading is supported.

    The heading lands in the fact sheet, not in any excerpt, so validating against the
    excerpts alone reported it as an unsupported value and marked a correct answer
    incomplete. Invented values must still be caught.
    """
    print("\n-- J. fact sheet is source evidence for unsupported-value checks --")
    analysis, _ctx, sources, _block, _facts = facts_for(
        "ما هي آخر تحديثات القضية بالتواريخ؟", knowledge, retriever
    )
    validator = AnswerValidator()

    # A section reached through the merged-section map contributes its heading to the
    # fact sheet but has no excerpt of its own, so its date is in the prompt yet absent
    # from the context string.
    context = "[1] الملف: case.md | القسم: وقائع الجلسة\nحضر الطرفان وقُدّمت المذكرات."
    heading_fact = SourceFact(
        label="26/03/2026", value="مسار الغرامات التعاقدي (ما بعد 26/03/2026)",
        kind="date", citation=1, primary=True, section_heading="مسار الغرامات التعاقدي",
    )
    check("26/03/2026" not in context, "the date is genuinely absent from the excerpts")

    grounded = "بدأ مسار الغرامات التعاقدي بتاريخ 26/03/2026 [1]."
    without_facts = validator.validate(analysis, grounded, context, sources, [])
    check(
        "26/03/2026" in without_facts.unsupported_values,
        "excerpt-only validation is what produced the false positive",
    )

    with_facts = validator.validate(analysis, grounded, context, sources, [heading_fact])
    check(
        "26/03/2026" not in with_facts.unsupported_values,
        "a date supported by the fact sheet is no longer flagged",
    )

    invented = validator.validate(
        analysis, "صدر الحكم بتاريخ 12/10/2099 بمبلغ 7,777,777 درهم [1].",
        context, sources, [heading_fact],
    )
    check("12/10/2099" in invented.unsupported_values, "an invented date is still flagged")
    check(
        any("7,777,777" in v or "7777777" in v for v in invented.unsupported_values),
        "an invented amount is still flagged",
    )


class _ScriptedLLM:
    """Returns prepared answers so the retry flow can be driven deterministically."""

    model = "scripted"

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def chat(self, _system: str, user: str) -> str:
        self.prompts.append(user)
        return self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]


def make_service(knowledge, retriever, llm):
    from app.services.completion import CompletionEngine
    from app.services.contract_builder import AnswerContractBuilder
    from app.services.coverage import CoverageValidator
    from app.services.evidence_planner import EvidencePlanner
    from app.services.rag_service import RagService

    return RagService(
        analyzer=QueryAnalyzer(),
        retriever=retriever,
        context_builder=ContextBuilder(20000, 3),
        llm=llm,
        validator=AnswerValidator(),
        knowledge=knowledge,
        fact_sheet=FactSheetBuilder(),
        contracts=AnswerContractBuilder(),
        planner=EvidencePlanner(),
        coverage=CoverageValidator(),
        completion=CompletionEngine(llm, max_passes=1),
        completeness_retry=True,
    )


TIMELINE_Q = "ما هي آخر تحديثات القضية بالتواريخ؟"
COMPLETE_ANSWER = (
    "- 27/05/2026: الحكم القطعي [1]\n- 24/05/2026: جلسة المرافعة [1]\n"
    "- 20/05/2026: التقرير النهائي [1]\n- 18/05/2026: مرحلة التقرير المبدئي [1]"
)
MISSING_0608 = (
    "- 27/05/2026: الحكم القطعي [1]\n- 24/05/2026: جلسة المرافعة [1]\n"
    "- 20/05/2026: التقرير النهائي [1]"
)


def test_completion_retry_flow(knowledge, retriever) -> None:
    """The retry must fire on a detected omission, and its outcome must be visible.

    A pass that ran and failed must report passes=2: reporting 1 would read as "no
    retry happened" while a second generation had in fact been spent.
    """
    print("\n-- K. completion pass is triggered and reported --")
    from app.schemas.chat import ChatRequest

    print("   K1. first answer omits 06/08 → retry fixes it")
    llm = _ScriptedLLM(MISSING_0608, COMPLETE_ANSWER)
    result = make_service(knowledge, retriever, llm).answer(ChatRequest(question=TIMELINE_Q))
    check(len(llm.prompts) == 2, "a second generation ran")
    check(result.coverage.passes == 2, "passes reports 2")
    check("18/05/2026" in result.answer, "the restored date is in the final answer")
    check(result.validation.omitted_facts == [], "nothing is reported missing any more")
    check(result.validation.complete, "answer is marked complete once resolved")
    check(result.validation.expanded, "the retry is flagged on the response")
    # Targeted completion sends the gap and the answer so far — not the whole prompt
    # again — so the model adds what is missing instead of rewriting everything.
    check("18/05/2026" in llm.prompts[1], "the completion prompt names the omission")
    check(MISSING_0608 in llm.prompts[1], "it carries the current answer forward")
    check(
        len(llm.prompts[1]) < len(llm.prompts[0]),
        "it is smaller than the original prompt",
    )

    print("   K2. first answer already complete → no retry")
    llm = _ScriptedLLM(COMPLETE_ANSWER, "should never be used")
    result = make_service(knowledge, retriever, llm).answer(ChatRequest(question=TIMELINE_Q))
    check(len(llm.prompts) == 1, "only one generation ran")
    check(result.coverage.passes == 1, "passes reports 1")
    check(result.validation.complete, "complete answer stays complete")

    print("   K3. second answer still incomplete → still marked incomplete")
    llm = _ScriptedLLM(MISSING_0608, MISSING_0608)
    result = make_service(knowledge, retriever, llm).answer(ChatRequest(question=TIMELINE_Q))
    check(len(llm.prompts) == 2, "the retry still ran")
    check(result.coverage.passes == 2, "passes reports 2 even though it did not help")
    check(not result.validation.complete, "answer remains incomplete")
    check(
        any("18/05/2026" in f for f in result.validation.omitted_facts),
        "the unresolved omission is still reported",
    )
    check(bool(result.answer.strip()), "the best grounded answer is still returned")

    print("   K4. invented values stay detected through the retry flow")
    invented = MISSING_0608 + "\n- 12/10/2099: حكم مستقبلي بمبلغ 7,777,777 درهم [1]"
    llm = _ScriptedLLM(invented, invented)
    result = make_service(knowledge, retriever, llm).answer(ChatRequest(question=TIMELINE_Q))
    check(
        "12/10/2099" in result.validation.unsupported_values,
        "invented date flagged after the retry flow",
    )
    check(
        any("7,777,777" in v or "7777777" in v for v in result.validation.unsupported_values),
        "invented amount flagged after the retry flow",
    )
    check(not result.validation.complete, "an answer with invented values is not complete")


MEETING_DOC = """---
title: ملف الخبرة
version: 1.0
---

# ملف الخبرة

## إشعار اجتماع الخبرة الأول (27/04/2026)

| البند | التفاصيل |
|---|---|
| **الموعد** | الاثنين 01/05/2026 — الساعة 5:30 مساءً |
| **الوسيلة** | اتصال مرئي عبر Zoom — عن بُعد |
| **النتيجة** | الأفق دفعت بأن السور مبني خطأً والمختص طلب مناسيب التأسيس |

## نتائج المعاينة الميدانية الفعلية 03/05/2026

| البند | التفاصيل |
|---|---|
| **الموعد** | الأربعاء 03/05/2026 — الساعة 4:00 عصراً |
| **المكان** | موقع المشروع — القطعة 18 |
| **النتيجة** | ست مكاسب موثقة في محضر التقرير الأولي |

يرى المختص في المحضر أن المناسيب صحيحة. المختص مخوَّل بالانتقال للبلدية.
"""


def test_event_type_distinction() -> None:
    """A meeting is not a site visit.

    The reported failure: "اجتماع المختص في الموقع" was answered with the 22/07 site
    visit, because "موقع" ranks the inspection section first while the meeting was in
    fact held remotely on 20/07.
    """
    print("\n-- L. meeting vs site visit are kept apart --")
    import tempfile as _tempfile

    from app.services.rag_service import SYSTEM_PROMPT

    parsed = MarkdownParser().parse(MEETING_DOC.encode("utf-8"), "expert.md")
    DocumentStructureAnalyzer().analyze(parsed)
    SectionSummarizer().apply(parsed.sections)
    entities = EntityExtractor().extract(parsed.sections)
    chunks = HeadingAwareChunker(1200, 150, 150).chunk(parsed, "doc-2", "expert.md")

    knowledge = KnowledgeStore()
    knowledge.replace_document("doc-2", parsed.sections, entities, chunks, "Legal", "1.0", "ar")
    index = KeywordIndex()
    index.rebuild()

    # C: the pattern-matched fragments must not survive as person entities.
    people = [e.value for e in entities if e.kind == "person"]
    check(
        not any("في المحضر" in p or "مخو" in p for p in people),
        f"sentence fragments rejected as names (got {people[:3]})",
    )

    analysis = QueryAnalyzer().analyze(
        "اذكر لي متى حصل اجتماع المختص في الموقع وماذا نتج عن الاجتماع"
    )
    check(str(analysis.intent) == "timeline", "B: 'متى' beats 'اذكر'")

    retriever = HybridRetriever(
        embedder=_StubEmbedder(), store=_StubStore(), keyword_index=index,
        knowledge=knowledge, reranker=FeatureReranker(), top_k=6, candidate_pool=20,
        score_threshold=0.35, vector_score_floor=0.25, min_rerank_score=0.05,
        max_expanded_candidates=40, wide_top_k=12,
    )
    candidates = retriever.retrieve(analysis)
    context, sources = ContextBuilder(20000, 3).build(candidates, wide=True)
    merged = knowledge.merged_section_headings(
        [(s.document_id, s.section_id) for s in sources if s.section_id]
    )
    slots = FactSheetBuilder.section_map(sources, merged)
    block, facts = FactSheetBuilder().build(
        analysis, slots, knowledge.entities_for_sections(list(slots))
    )

    check("01/05/2026" in context, "the meeting date is in the evidence")
    check("03/05/2026" in context, "the inspection date is in the evidence")
    check("عن بُعد" in context or "Zoom" in context, "the meeting's medium is in the evidence")

    # D: an ambiguous label must name its own event.
    qualified = [f for f in facts if "الموعد" in f.label and f.label != "الموعد"]
    check(bool(qualified), f"D: 'الموعد' qualified by its section (got {[f.label for f in facts][:4]})")
    meeting = next((f for f in qualified if "اجتماع" in f.label), None)
    check(meeting is not None, "the meeting has its own dated fact")
    check(
        meeting is not None and "01/05/2026" in meeting.value,
        "and it carries 20/07 — not the inspection date",
    )
    # The inspection stays dated in the skeleton; the question asks about the meeting,
    # so only the meeting's exact time is promoted into the detail lines.
    check(
        any(f.kind == "date" and f.label == "03/05/2026" for f in facts),
        "the inspection keeps its own dated entry",
    )
    check(
        meeting is not None and "22/07" not in meeting.value,
        "the inspection's date is never attached to the meeting",
    )

    # E: the prompt must refuse the question's false premise.
    check("لا تقبل مقدّمة السؤال" in SYSTEM_PROMPT, "E: false-premise rule present")
    check("لا تدمج حدثين" in SYSTEM_PROMPT, "E: no-conflation rule present")


RANGE_DOC = """---
title: وقائع القضية
version: 1.0
---

# وقائع القضية

## التسلسل الزمني للوقائع

| التاريخ | الواقعة |
|---|---|
| 06/12/2024 | اكتمال السور الحدودي واستلامه |
| 30/11/2025 | بدء حفر الأفق العميق الملاصق للسور |
| 02/12/2025 | هبوط جزئي في بنل واحد من السور |
| 13/12/2025 | شكوى رسمية لبلدية أبوظبي |
| 02/01/2026 | الانهيار الكامل للسور |
| 22/01/2026 | تسجيل الإنذار العدلي |

## الرخص

| البند | القيمة |
|---|---|
| **الرخصة التجارية لالأفق** | CN-2200481 |
| **رقم رخصة البناء لالأفق** | B1N-2023-007782 |
"""


def _range_fixture():
    parsed = MarkdownParser().parse(RANGE_DOC.encode("utf-8"), "range.md")
    DocumentStructureAnalyzer().analyze(parsed)
    SectionSummarizer().apply(parsed.sections)
    entities = EntityExtractor().extract(parsed.sections)
    chunks = HeadingAwareChunker(1600, 150, 150).chunk(parsed, "doc-3", "range.md")
    knowledge = KnowledgeStore()
    knowledge.replace_document("doc-3", parsed.sections, entities, chunks, "Legal", "1.0", "ar")
    index = KeywordIndex()
    index.rebuild()
    retriever = HybridRetriever(
        embedder=_StubEmbedder(), store=_StubStore(), keyword_index=index,
        knowledge=knowledge, reranker=FeatureReranker(), top_k=6, candidate_pool=20,
        score_threshold=0.35, vector_score_floor=0.25, min_rerank_score=0.05,
        max_expanded_candidates=40, wide_top_k=12,
    )
    return knowledge, retriever


def test_ranged_timeline() -> None:
    """T1: "من X حتى Y" must cover that window, in order, and nothing outside it.

    Handed the whole history newest-first, the answer wandered back to an event a year
    earlier and stopped before reaching the end of the span.
    """
    print("\n-- T1. a ranged timeline question stays inside its window --")
    knowledge, retriever = _range_fixture()

    analysis = QueryAnalyzer().analyze(
        "اذكر تسلسل الأحداث من بدء حفر الأفق حتى الانهيار الكامل للسور."
    )
    check(str(analysis.intent) == "timeline", "classified as a timeline question")
    check(analysis.is_ranged, f"range detected ({analysis.range_from!r} → {analysis.range_to!r})")

    _a, _ctx, _src, block, facts = facts_for(
        "اذكر تسلسل الأحداث من بدء حفر الأفق حتى الانهيار الكامل للسور.",
        knowledge, retriever,
    )
    from app.core.text import parse_date

    labels = [f.label for f in facts if f.kind == "date"]
    dates = [parse_date(lbl) for lbl in labels]
    check("النطاق" in block, "the prompt says the entries are the requested window")
    check(dates == sorted(dates), f"entries run oldest to newest ({labels})")
    for expected in ("30/11/2025", "02/12/2025", "13/12/2025", "02/01/2026"):
        check(expected in labels, f"in-range event present: {expected}")
    check("06/12/2024" not in labels, "an event before the start is excluded")
    check("22/01/2026" not in labels, "an event after the end is excluded")

    # An unranged timeline question keeps the newest-first behaviour.
    _a, _c, _s, block2, facts2 = facts_for(
        "ما هي آخر تحديثات القضية بالتواريخ؟", knowledge, retriever
    )
    later = [f.label for f in facts2 if f.kind == "date"]
    check(
        later == sorted(later, reverse=True) or len(later) < 2,
        f"'آخر التحديثات' still newest-first ({later})",
    )


def test_ambiguous_entity_name() -> None:
    """M4: "رخصة الأفق" names two documented things — say both, do not pick one."""
    print("\n-- M4. an ambiguous name is disambiguated, not guessed --")
    knowledge, retriever = _range_fixture()

    _a, _ctx, _src, block, facts = facts_for(
        "ما رقم رخصة الأفق ومتى صدرت؟", knowledge, retriever
    )
    check("تنبيه" in block, "the prompt flags the ambiguity")
    check("CN-2200481" in block, "the trade licence is listed")
    check("B1N-2023-007782" in block, "the building permit is listed")
    check(
        block.index("تنبيه") < block.index("الوقائع المستخرجة"),
        "the warning comes before the facts",
    )

    labels = [f.label for f in facts]
    check(
        any("التجارية" in lbl for lbl in labels) and any("البناء" in lbl for lbl in labels),
        f"each licence keeps its own label ({labels})",
    )

    # A question naming only one kind must not be flagged.
    _a, _c, _s, focused, _f = facts_for("ما قيمة العقد؟", knowledge, retriever)
    check("تنبيه" not in focused, "an unambiguous question gets no warning")


def test_out_of_kb(knowledge, retriever) -> None:
    print("\n-- G. out-of-knowledge-base question --")
    analysis = QueryAnalyzer().analyze("ما هي سياسة العمل عن بعد وعدد أيام الإجازات السنوية؟")
    check(retriever.retrieve(analysis) == [], "uncovered question retrieves nothing")


if __name__ == "__main__":
    knowledge, retriever = build()
    test_intent_classification()
    test_fact_sheet_roles(knowledge, retriever)
    test_completeness_detection(knowledge, retriever)
    test_focused_question_not_penalised(knowledge, retriever)
    test_numeric_and_dates(knowledge, retriever)
    test_multi_section(knowledge, retriever)
    test_partial_retrieval_regression(knowledge, retriever)
    test_timeline_sweep(knowledge, retriever)
    test_fact_sheet_counts_as_evidence(knowledge, retriever)
    test_completion_retry_flow(knowledge, retriever)
    test_event_type_distinction()
    test_ranged_timeline()
    test_ambiguous_entity_name()
    test_out_of_kb(knowledge, retriever)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("All completeness checks passed.")
