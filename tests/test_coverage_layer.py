"""Unit checks for the evidence coverage layer.

Every case here is written as a *shape* of question — one entity, an enumeration, a
number, a date, a span, several parts, a comparison — never as a particular question
from the evaluation set. A rule that only worked for one wording would pass none of
these twice.

Run: python tests/test_coverage_layer.py
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

_TMP = Path(tempfile.mkdtemp(prefix="rag-coverage-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")

from app.core.contract import RequirementKind  # noqa: E402
from app.core.evidence import EvidenceItem, EvidenceSet  # noqa: E402
from app.schemas.chat import SourceFact, SourceReference  # noqa: E402
from app.services.completion import CompletionEngine  # noqa: E402
from app.services.contract_builder import AnswerContractBuilder  # noqa: E402
from app.services.coverage import CoverageValidator  # noqa: E402
from app.services.evidence_planner import EvidencePlanner  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402

FAILURES: list[str] = []

ANALYZER = QueryAnalyzer()
CONTRACTS = AnswerContractBuilder()
PLANNER = EvidencePlanner()
COVERAGE = CoverageValidator()


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")


def contract_for(question: str):
    return CONTRACTS.build(ANALYZER.analyze(question))


def item(kind, value, label="", citation=1, heading="", primary=True) -> EvidenceItem:
    return EvidenceItem(
        kind=kind, value=value, label=label, citation=citation,
        section_heading=heading, primary=primary,
    )


def evidence_of(*items: EvidenceItem) -> EvidenceSet:
    evidence = EvidenceSet(context="")
    for entry in items:
        evidence.add(entry)
    return evidence


def cover(question: str, items: tuple[EvidenceItem, ...], answer: str):
    contract = contract_for(question)
    bound = PLANNER.bind(contract, evidence_of(*items))
    return contract, COVERAGE.check(contract, bound, answer)


# ---------------------------------------------------------------- contract


def test_contract_shapes() -> None:
    print("\n-- AnswerContract: requirements come from question shape --")

    single = contract_for("من هو المدير المسؤول عن المشروع؟")
    check(
        any(r.kind is RequirementKind.PERSON for r in single.requirements),
        "A. 'من هو' owes a person",
    )
    check(not any(r.enumeration for r in single.requirements), "A. singular is not a list")

    listed = contract_for("من هم الأطراف المذكورون؟")
    check(
        any(r.kind is RequirementKind.PERSON and r.enumeration for r in listed.requirements),
        "B. 'من هم' owes every supported person",
    )

    numeric = contract_for("ما رقم السجل الخاص بالمكتب؟")
    check(
        any(r.kind is RequirementKind.NUMBER for r in numeric.requirements),
        "C. 'رقم' owes a number",
    )

    dated = contract_for("متى جرى التسليم؟")
    check(
        any(r.kind is RequirementKind.DATE for r in dated.requirements),
        "D. 'متى' owes a date",
    )

    span = contract_for("اذكر الأحداث من البداية حتى النهاية بالتواريخ")
    check(span.chronological, "E. a dated sequence is chronological")
    check(
        any(r.kind is RequirementKind.DATE and r.enumeration for r in span.requirements),
        "E. and owes every date",
    )

    multi = contract_for("من حضر الاجتماع، ومتى عُقد، وماذا نتج عنه؟")
    kinds = {r.kind for r in multi.requirements}
    check(multi.multi_part, "F. several asks are seen as several parts")
    check(
        {RequirementKind.PERSON, RequirementKind.DATE, RequirementKind.EVENT} <= kinds,
        f"F. one requirement per ask ({sorted(str(k) for k in kinds)})",
    )

    company = contract_for("من هي الشركات المذكورة في الملف؟")
    check(
        any(r.kind is RequirementKind.ORGANIZATION for r in company.requirements),
        "G. 'من' about companies owes organisations, not people",
    )

    english = contract_for("Who signed the document and when?")
    kinds = {r.kind for r in english.requirements}
    check(
        RequirementKind.PERSON in kinds and RequirementKind.DATE in kinds,
        f"H. English questions are handled the same way ({sorted(str(k) for k in kinds)})",
    )


# ---------------------------------------------------------------- planner


def test_planner_binding() -> None:
    print("\n-- EvidencePlanner: candidates are bound by kind and wording --")
    items = (
        item("attribute", "المهندس سالم أحمد", "مدير المشروع", heading="فريق العمل"),
        item("attribute", "2,680,000 درهم", "قيمة العقد", heading="الأرقام"),
        item("attribute", "شركة ألفا للمقاولات", "المقاول", heading="فريق العمل"),
    )
    contract = contract_for("من هو مدير المشروع؟")
    bound = PLANNER.bind(contract, evidence_of(*items))
    values = [i.value for group in bound.values() for i in group]
    check("المهندس سالم أحمد" in values, "the matching person is bound")
    check("2,680,000 درهم" not in values, "an unrelated amount is not bound to a person ask")

    contract = contract_for("ما قيمة العقد؟")
    bound = PLANNER.bind(contract, evidence_of(*items))
    values = [i.value for group in bound.values() for i in group]
    check("2,680,000 درهم" in values, "the matching amount is bound")

    check(
        not evidence_of(item("attribute", "س", "قصير")).add(
            item("attribute", "س", "قصير")
        ),
        "N. duplicate evidence is added once")


# ---------------------------------------------------------------- coverage


def test_coverage_matching() -> None:
    print("\n-- CoverageValidator: matching follows the requirement kind --")

    _c, result = cover(
        "متى صدر القرار؟",
        (item("date", "صدور القرار", "10/02/2026", heading="القرارات"),),
        "صدر القرار في 10 فبراير 2026 [1].",
    )
    check(result.complete, "D. a date written in words counts as stated")

    _c, result = cover(
        "متى صدر القرار؟",
        (item("date", "صدور القرار", "10/02/2026", heading="القرارات"),),
        "صدر القرار في 11/02/2026 [1].",
    )
    check(not result.complete, "D. a different date does not count")

    _c, result = cover(
        "ما قيمة العقد؟",
        (item("attribute", "2,680,000 درهم", "قيمة العقد", heading="الأرقام"),),
        "قيمة العقد 2680000 درهم [1].",
    )
    check(result.complete, "C. an amount without separators counts")

    _c, result = cover(
        "من هو المدير المسؤول؟",
        (item("attribute", "المهندس سالم أحمد الكعبي", "المدير المسؤول", heading="الفريق"),),
        "المدير المسؤول هو سالم أحمد الكعبي [1].",
    )
    check(result.complete, "K. a name stated without its title still counts")

    _c, result = cover(
        "من هم الأطراف؟",
        (
            item("attribute", "شركة ألفا للمقاولات", "الطرف الأول", heading="الأطراف"),
            item("attribute", "شركة بيتا للتجارة", "الطرف الثاني", heading="الأطراف"),
            item("attribute", "المهندس سالم أحمد", "ممثل الأطراف", heading="الأطراف"),
        ),
        "الأطراف هما شركة ألفا للمقاولات [1] وشركة بيتا للتجارة [1].",
    )
    check(not result.complete, "B. an enumeration missing one member is incomplete")
    check(
        any("سالم" in i.value for i in result.missing),
        "B. and the missing member is named",
    )

    _c, result = cover(
        "من هم الأطراف؟",
        (
            item("attribute", "شركة ألفا للمقاولات", "الطرف الأول", heading="الأطراف"),
            item("attribute", "شركة بيتا للتجارة", "الطرف الثاني", heading="الأطراف"),
        ),
        "الأطراف: شركة ألفا للمقاولات [1]، وشركة بيتا للتجارة [1].",
    )
    check(result.complete, "B. a complete enumeration passes")

    _c, result = cover(
        "من وقّع العقد ومتى؟",
        (
            item("attribute", "المهندس سالم أحمد", "الموقّع", heading="التوقيع"),
            item("date", "توقيع العقد", "09/10/2024", heading="التوقيع"),
        ),
        "وقّع العقد المهندس سالم أحمد [1].",
    )
    check(not result.complete, "F. a part left unanswered is detected")
    check(
        len(result.unmet) == 1,
        f"F. only the unanswered part is unmet ({[e.requirement.key for e in result.unmet]})",
    )

    _c, result = cover(
        "ما قيمة العقد؟",
        (item("attribute", "2,680,000 درهم", "قيمة العقد", heading="الأرقام"),),
        "قيمة العقد 2,680,000 درهم [1]. ولا معلومات أخرى.",
    )
    check(result.score == 1.0, "a satisfied contract scores 1.0")


def test_no_evidence_no_demand() -> None:
    print("\n-- nothing is demanded that the evidence does not support --")
    _c, result = cover(
        "من هو المستشار القانوني؟",
        (item("attribute", "2,680,000 درهم", "قيمة العقد", heading="الأرقام"),),
        "لا توجد معلومات كافية.",
    )
    check(result.complete, "I. an unsupported ask raises no missing item")
    check(result.missing == [], "I. and nothing is sent to completion")


def test_conflicting_and_long() -> None:
    print("\n-- conflicting and many-item evidence --")
    _c, result = cover(
        "ما قيمة الكشف المالي؟",
        (
            item("attribute", "121,095 درهم", "الكشف المالي الأصلي", heading="الأرقام"),
            item("attribute", "126,788 درهم", "الكشف المالي المعاد حسابه", heading="الأرقام"),
        ),
        "الكشف المالي الأصلي 121,095 درهم [1]، والمعاد حسابه 126,788 درهم [1].",
    )
    check(result.complete, "M. both sides of a conflict count as stated")

    many = tuple(
        item("attribute", f"شركة رقم {n} المحدودة", f"الطرف {n}", heading="الأطراف")
        for n in range(1, 9)
    )
    listed = "، ".join(f"شركة رقم {n} المحدودة [1]" for n in range(1, 9))
    _c, result = cover("من هم الأطراف؟", many, f"الأطراف: {listed}.")
    check(result.complete, "O. a long answer covering every item passes")

    _c, result = cover("من هم الأطراف؟", many, "الأطراف: شركة رقم 1 المحدودة [1].")
    check(len(result.missing) >= 5, "O. and a short answer reports the rest as missing")


def enumeration_is_per_part() -> None:
    """A list cue in one part does not make every other part a list.

    Regression: a question ending in a distributive ("...ومتى صدرت كل منهما") set the
    question-level exhaustive flag, which turned each specific ask into a demand for
    every value the evidence carried — including an address and a person, because both
    contain digits. Completion then rewrote a correct answer to chase them.
    """
    print("\n-- enumeration is decided per ask, not per question --")

    contract = CONTRACTS.build(ANALYZER.analyze("ما رقم رخصة الأولى وما رقم الثانية وما تاريخهما جميعاً؟"))
    check(len(contract.requirements) >= 2, "Q. the question splits into several asks")
    check(not contract.requirements[0].enumeration,
          "Q. a specific ask stays singular despite a list cue elsewhere")

    single = CONTRACTS.build(ANALYZER.analyze("اذكر جميع أرقام الرخص."))
    check(single.requirements[0].enumeration,
          "Q. a single-part question keeps the question-level list signal")


def completion_may_only_add() -> None:
    """A rewrite that states fewer of the retrieved values is rejected."""
    print("\n-- a completion pass may add, never lose --")

    evidence = EvidenceSet(context="")
    for value, label in (
        ("B1N-2024-005221-P01", "رخصة البناء الأولى"),
        ("B1N-2025-016103-P01", "رخصة البناء الثانية"),
        ("04/02/2026", "تاريخ الإصدار الثاني"),
    ):
        evidence.add(EvidenceItem(kind="identifier", value=value, label=label, citation=1))

    full = "الأولى B1N-2024-005221-P01 [1]، والثانية B1N-2025-016103-P01 [1] بتاريخ 04/02/2026 [1]."
    trimmed = "الأولى B1N-2024-005221-P01 [1]."
    check(COVERAGE.stated_count(evidence, full) == 3, "R. every stated value is counted")
    check(COVERAGE.stated_count(evidence, trimmed) == 1, "R. a rewrite that drops values counts lower")
    check(COVERAGE.stated_count(evidence, trimmed) < COVERAGE.stated_count(evidence, full),
          "R. so the losing rewrite is detectable before it is accepted")


def shape_only_requirements() -> None:
    """A requirement no evidence echoes is checked by shape, not by a chosen value.

    Regression: a single-date question whose evidence shares no wording with it used to
    bind an arbitrary date and then report the *correct* answer as missing it, which sent
    a right answer to completion and had it overwritten with the wrong date.
    """
    print("\n-- a requirement without a lexical anchor is judged by shape --")

    unrelated_dates = (
        item("date", "28/11/2024", "بدء أعمال الطرف الآخر", heading="سجل الإصدارات"),
        item("date", "07/07/2026", "مذكرة دفاع", heading="سجل الجلسات"),
    )
    contract, result = cover(
        "متى بدأ الحفر العميق؟", unrelated_dates, "بدأ الحفر العميق في 18/02/2026 [3]."
    )
    requirement = contract.by_key("p0.date")
    check(requirement is not None and requirement.shape_only,
          "P. a date ask with no matching wording is marked shape-only")
    check(result.complete, "P. an answer that does state a date is not called incomplete")
    check(not result.missing, "P. and no date is demanded of it")

    _c, result = cover(
        "متى بدأ الحفر العميق؟", unrelated_dates, "بدأ الحفر العميق بعد صدور التصريح [3]."
    )
    check(not result.complete, "P. an answer stating no date at all is still unmet")

    # The same rule must not soften a requirement the evidence *does* echo.
    anchored = (item("date", "18/02/2026", "بدء الحفر العميق", heading="التسلسل الزمني"),)
    contract, result = cover(
        "متى بدأ الحفر العميق؟", anchored, "بدأ الحفر العميق في 28/11/2024 [1]."
    )
    check(not contract.by_key("p0.date").shape_only,
          "P. matching wording keeps the requirement value-bound")
    check(not result.complete, "P. and a wrong date is reported against it")


# ---------------------------------------------------------------- completion


class _RecordingLLM:
    model = "recording"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def chat(self, _system: str, user: str) -> str:
        self.prompts.append(user)
        return self.reply


def test_completion_engine() -> None:
    print("\n-- CompletionEngine: targeted, and only when needed --")
    contract, result = cover(
        "من هم الأطراف؟",
        (
            item("attribute", "شركة ألفا للمقاولات", "الطرف الأول", heading="الأطراف"),
            item("attribute", "شركة بيتا للتجارة", "الطرف الثاني", heading="الأطراف"),
        ),
        "الأطراف: شركة ألفا للمقاولات [1].",
    )
    llm = _RecordingLLM("الأطراف: شركة ألفا للمقاولات [1] وشركة بيتا للتجارة [1].")
    completed = CompletionEngine(llm, max_passes=1).complete(
        contract, result, "الأطراف: شركة ألفا للمقاولات [1]."
    )
    check(completed is not None, "J. a gap triggers completion")
    check(len(llm.prompts) == 1, "J. exactly one extra generation")
    check("بيتا" in llm.prompts[0], "J. the prompt carries the missing evidence")
    check("ألفا" in llm.prompts[0], "J. and the answer so far")

    _c2, satisfied = cover(
        "من هم الأطراف؟",
        (item("attribute", "شركة ألفا للمقاولات", "الطرف الأول", heading="الأطراف"),),
        "الأطراف: شركة ألفا للمقاولات [1].",
    )
    idle = _RecordingLLM("unused")
    check(
        CompletionEngine(idle, max_passes=1).complete(_c2, satisfied, "…") is None,
        "a satisfied contract runs no completion",
    )
    check(idle.prompts == [], "and costs no generation")


if __name__ == "__main__":
    test_contract_shapes()
    test_planner_binding()
    test_coverage_matching()
    test_no_evidence_no_demand()
    test_conflicting_and_long()
    shape_only_requirements()
    enumeration_is_per_part()
    completion_may_only_add()
    test_completion_engine()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("All coverage-layer checks passed.")
