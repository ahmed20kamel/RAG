"""The live-data hint: what it must catch, and what it must not.

This exists because the first version was a substring search, and "الآن" is a substring
of "الانصراف". A question about the attendance policy came back flagged as a request for
live operational data. The hint is advisory, so the cost was small — but a signal that
fires on unrelated questions stops being read, and then it is not a signal.

The second half of this file is the half that matters. Anyone can make a word list match
the words in it; the work is in not matching the words that merely contain them.

Run: python tests/test_live_data_signal.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.services.live_data import (  # noqa: E402
    matched_markers,
    needs_live_data,
    suggested_source,
)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


#: Questions only the operational system can answer.
OPERATIONAL = [
    "كم عدد أوامر الشراء هذا الشهر؟",
    "ما إجمالي المشتريات هذا الشهر؟",
    "ما حالة المورد الحالية؟",
    "ما الرصيد الآن؟",
    "ما المخزون المتاح؟",
    "ما الفواتير المستحقة؟",
    "كم بلغ الصرف اليوم؟",
    "How many purchase orders were raised this month?",
    "What is the current inventory level?",
    "Show me outstanding invoices",
]

#: Questions the documents answer, several of them containing a marker as a fragment of
#: a longer word. These are the regressions.
DOCUMENTARY = [
    # "الانصراف" contains "الان" — the original defect.
    "ما سياسة الحضور والانصراف في الشركة؟",
    "ما إجراءات المطالبات والتأخير وفق عقد FIDIC؟",
    "ما هي متطلبات الحفر العميق والتدعيم؟",
    "ما سياسة المشتريات المعتمدة؟",
    # "الإنتاجية" normalises to something containing "الان"-like sequences.
    "ما الإنتاجية المتوقعة للمشروع؟",
    "ما هي الاناقة المطلوبة في التشطيبات؟",
    "ما شروط الضمان في العقد؟",
    "من هو المقاول الرئيسي للمشروع؟",
    "What does the method statement require for shoring?",
    "What are the contractual notice requirements?",
]


def operational_questions_are_flagged() -> None:
    print("\n-- questions that need the operational system --")
    for question in OPERATIONAL:
        flagged = needs_live_data(question)
        check(flagged, f"flagged: {question}",
              f"markers found: {matched_markers(question)}")
        if flagged:
            check(suggested_source(question) == "erp",
                  f"points at ERP: {question}")


def documentary_questions_are_not_flagged() -> None:
    print("\n-- questions the documents answer --")
    for question in DOCUMENTARY:
        flagged = needs_live_data(question)
        check(not flagged, f"not flagged: {question}",
              f"matched: {matched_markers(question)}")
        if not flagged:
            check(suggested_source(question) is None,
                  f"suggests nothing: {question}")


def edges() -> None:
    print("\n-- edges --")
    check(needs_live_data("") is False, "an empty question is not flagged")
    check(needs_live_data("   ") is False, "whitespace is not flagged")
    check(suggested_source("") is None, "an empty question suggests nothing")
    # Diacritics, presentation forms and Arabic-Indic digits all normalise away before
    # matching, so the same question written three ways gives the same answer.
    variants = ["ما الرصيد الآن؟", "ما الرصيد الان", "ما الرَّصيد الآنَ؟"]
    check(all(needs_live_data(v) for v in variants),
          "the same question written three ways is flagged each time",
          str([(v, needs_live_data(v)) for v in variants]))


def main() -> None:
    print("live-data signal")
    operational_questions_are_flagged()
    documentary_questions_are_not_flagged()
    edges()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("the signal fires on operational questions and stays quiet on the rest")


if __name__ == "__main__":
    main()
