"""What counts as two sources contradicting each other, and what does not.

The measured failure: `conflict_count` reached its ceiling of twelve on every question
asked of the system, including unrelated ones about different documents. The detector
was collecting every digit run in a passage — section numbers, reference codes, revision
numbers, dates — and calling two passages contradictory whenever they shared a couple of
common words and differed somewhere in those digits. In a working file full of tables,
that describes almost any two passages.

Twelve unresolved contradictions then travelled into the prompt with the note that
nothing could settle them, so the model was being handed a page of false alarms
alongside its evidence on every single question.

A conflict now requires the same attribute on both sides — the words naming what was
measured — a compatible unit, and different values. This suite holds that line from both
directions: the real contradictions in `test_authority_and_conflicts.py` must keep being
found, and the shapes below must stop being reported.

Deterministic and offline: no database, no model, no network.

Run: python tests/test_conflict_precision.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.services.conflict_detector import (  # noqa: E402
    MAX_CONFLICTS,
    ConflictDetector,
    _measurements,
)

FAILURES: list[str] = []
DETECTOR = ConflictDetector()


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def doc(citation: int, excerpt: str, document_id: str = "") -> object:
    class _Source:
        def __init__(self) -> None:
            self.citation = citation
            self.excerpt = excerpt
            self.filename = f"f{citation}.md"
            self.document_id = document_id or f"d{citation}"
            self.version = ""

    return _Source()


def conflicts(*excerpts: str) -> list:
    return DETECTOR.detect([doc(i + 1, e) for i, e in enumerate(excerpts)], [])


def described(items: list) -> str:
    return " | ".join(c.describe()[:120] for c in items)


# ---------------------------------------------------------------------------
# 1. What a measurement is
# ---------------------------------------------------------------------------


def measurement_extraction() -> None:
    print("\n-- 1. which numbers are claims about something --")

    found = _measurements("قيمة غرامة التأخير القصوى للمشروع هي 50,000 درهم")
    check(len(found) == 1, f"a labelled amount is one measurement ({len(found)})")
    if found:
        check(found[0].value == "50000", "the value drops its thousands separators")
        check(found[0].unit == "درهم", "and keeps its unit", found[0].unit)

    # Everything below states a number and measures nothing.
    silent = [
        ("11. الملفات والتقارير المنتجة", "a section number"),
        ("| الإصدار | v1.4.4 | 02/04/2026 |", "a version and a date in a table row"),
        ("الرقم المرجعي fb/2026-147 بتاريخ 02/12/2025", "a reference code and a date"),
        ("صدر القرار في 27/05/2026", "a bare date"),
        ("اجتماع عام 2026", "a bare year"),
        ("الرقم 12", "a number with only one word naming it"),
        ("3) البند الثالث من المحضر", "a list marker"),
    ]
    for text, label in silent:
        found = _measurements(text)
        check(not found, f"not a measurement: {label}", f"{text!r} → {found}")


# ---------------------------------------------------------------------------
# 2. Shapes that must no longer be reported
# ---------------------------------------------------------------------------


def bookkeeping_noise_is_silent() -> None:
    print("\n-- 2. the shapes that filled the ceiling --")

    check(
        conflicts(
            "قائمة الملفات المنتجة: 11 ملف كامل و23 تقرير بحالة مكتملة.",
            "سجل إصدارات هذا الملف: الإصدار v1.4.4 بتاريخ 02/04/2026 أضاف 14 قسماً.",
        ) == [],
        "section counts against a version log raise nothing",
    )
    check(
        conflicts(
            "الرقم المرجعي fb/2026-147 بتاريخ 02/12/2025 بشأن الملف الكامل.",
            "الرقم المرجعي fb/2026-151 بتاريخ 16/12/2025 بشأن الملف الكامل.",
        ) == [],
        "two reference codes on the same subject raise nothing",
    )
    check(
        conflicts(
            "عُقدت الجلسة في 04/05/2026 بشأن المذكرة التكميلية للقضية.",
            "صدر الحكم في 27/05/2026 بشأن المذكرة التكميلية للقضية.",
        ) == [],
        "two different dates about the same matter are a sequence, not a contradiction",
    )
    check(
        conflicts(
            "تقرير المختص الأول عن المشروع صدر ضمن الملف الكامل رقم 2026.",
            "تقرير المختص الثاني عن المشروع صدر ضمن الملف الكامل رقم 2026.",
        ) == [],
        "a shared year raises nothing",
    )
    check(
        conflicts(
            "| البند | 6 | الحالة | مكتمل |",
            "| البند | 14 | الحالة | مكتمل |",
        ) == [],
        "table row numbers raise nothing",
    )


def different_units_are_not_rivals() -> None:
    print("\n-- 3. quantities of different kinds cannot contradict --")

    check(
        conflicts(
            "قيمة الدفعة المقدمة للمشروع 30 يوماً من تاريخ التوقيع.",
            "قيمة الدفعة المقدمة للمشروع 150,000 درهم.",
        ) == [],
        "a duration and an amount, however alike their labels, are not rivals",
    )


def the_ceiling_is_no_longer_reachable_by_noise() -> None:
    print("\n-- 4. a page of bookkeeping produces no conflicts at all --")

    rows = [
        f"| {i} | تقرير رقم fb/2026-{100 + i} | 0{1 + i % 9}/06/2026 | الحالة مكتملة | الملف كامل |"
        for i in range(1, 13)
    ]
    found = conflicts(*rows)
    check(
        found == [],
        f"twelve similar table rows raise nothing (was the ceiling of {MAX_CONFLICTS})",
        described(found),
    )


# ---------------------------------------------------------------------------
# 3. Real contradictions must survive
# ---------------------------------------------------------------------------


def real_contradictions_survive() -> None:
    print("\n-- 5. genuine disagreements are still found --")

    found = conflicts(
        "قيمة غرامة التأخير القصوى للمشروع هي 50,000 درهم.",
        "قيمة غرامة التأخير القصوى للمشروع هي 310,000 درهم.",
    )
    check(len(found) == 1, f"a disputed amount is reported ({len(found)})", described(found))
    if found:
        text = found[0].describe()
        check("50000" in text and "310000" in text, "both values are preserved", text)
        check("[1]" in text and "[2]" in text, "both citations are preserved", text)

    found = conflicts(
        "مدة الإشعار المسبق للمقاول 30 يوماً من واقعة التأخير.",
        "مدة الإشعار المسبق للمقاول 15 يوماً من واقعة التأخير.",
    )
    check(len(found) == 1, f"a disputed duration is reported ({len(found)})", described(found))

    found = conflicts(
        "نسبة الاحتجاز المطبقة على المستخلصات 10%.",
        "نسبة الاحتجاز المطبقة على المستخلصات 5%.",
    )
    check(len(found) == 1, f"a disputed percentage is reported ({len(found)})", described(found))


def one_conflict_per_attribute() -> None:
    print("\n-- 6. two disputed attributes give two conflicts, not a cross product --")

    found = conflicts(
        "قيمة الدفعة المقدمة للمشروع 150,000 درهم ومدة التنفيذ للمشروع 540 يوماً.",
        "قيمة الدفعة المقدمة للمشروع 120,000 درهم ومدة التنفيذ للمشروع 600 يوماً.",
    )
    check(len(found) == 2, f"exactly two conflicts ({len(found)})", described(found))
    subjects = {tuple(sorted(c.shared_terms)) for c in found}
    check(len(subjects) == 2, "each names a different attribute", str(subjects))


def agreement_is_not_conflict() -> None:
    print("\n-- 7. sources that agree raise nothing --")

    check(
        conflicts(
            "قيمة العقد الإجمالية للمشروع 1,450,000 درهم كما اعتُمدت.",
            "قيمة العقد الإجمالية للمشروع 1,450,000 درهم وفق الملحق الأول.",
        ) == [],
        "the same value stated twice raises nothing",
    )
    check(
        conflicts(
            "عدد العمال في الموقع 45 عاملاً في الوردية الصباحية.",
            "مدة التنفيذ المتبقية للمشروع 540 يوماً حتى التسليم.",
        ) == [],
        "different subjects raise nothing",
    )


def every_conflict_is_explainable() -> None:
    print("\n-- 8. every reported conflict names its basis --")

    found = conflicts(
        "قيمة غرامة التأخير القصوى للمشروع هي 50,000 درهم.",
        "قيمة غرامة التأخير القصوى للمشروع هي 310,000 درهم.",
    )
    check(bool(found), "a conflict was produced to inspect")
    if found:
        conflict = found[0]
        check(
            len(conflict.shared_terms) >= 2,
            "the attribute is named by at least two words",
            str(conflict.shared_terms),
        )
        check(
            "تعارض حول" in conflict.describe(),
            "and the description states what the disagreement is about",
        )


def main() -> None:
    print("conflict precision")
    measurement_extraction()
    bookkeeping_noise_is_silent()
    different_units_are_not_rivals()
    the_ceiling_is_no_longer_reachable_by_noise()
    real_contradictions_survive()
    one_conflict_per_attribute()
    agreement_is_not_conflict()
    every_conflict_is_explainable()

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("a conflict is two values for one attribute, and nothing else is")


if __name__ == "__main__":
    main()
