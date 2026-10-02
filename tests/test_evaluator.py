"""Checks for the evaluation harness itself.

Two real answers were scored as incomplete while being correct: one wrote a date in
words, the other used the Arabic rendering of a name the gold token spells in Latin.
Accepting those spellings must not loosen what counts as invented.

Run: python tests/test_evaluator.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.eval.run_eval import (  # noqa: E402
    contains,
    date_spellings,
    hallucinated_numbers,
    load_corpus,
)

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")


def test_date_equivalence() -> None:
    print("\n-- equivalent date spellings --")
    check("10 فبراير 2026" in date_spellings("10/02/2026"), "Arabic month form generated")
    check("2026-02-10" in date_spellings("10/02/2026"), "ISO form generated")
    check(date_spellings("not a date") == [], "non-dates produce nothing")
    check(date_spellings("10/13/2026") == [], "an impossible month produces nothing")

    check(contains("صدرت في 10 فبراير 2026", "10/02/2026"), "date in words matches")
    check(contains("صدرت في 10/02/2026", "10/02/2026"), "numeric date still matches")
    check(contains("issued on 10 February 2026", "10/02/2026"), "English month matches")
    check(not contains("صدرت في 11 فبراير 2026", "10/02/2026"), "a different day does not match")
    check(not contains("صدرت في 10 مارس 2026", "10/02/2026"), "a different month does not match")
    check(not contains("صدرت في 10 فبراير 2027", "10/02/2026"), "a different year does not match")


def test_name_equivalence() -> None:
    print("\n-- equivalent renderings of a name --")
    check(contains("مكتب فيوتشر بيلد للاستشارات", "Future Build"), "Arabic rendering matches")
    check(contains("Future Build Engineering", "Future Build"), "Latin form still matches")
    check(not contains("مكتب آخر للاستشارات", "Future Build"), "an unrelated office does not match")
    check(contains("سور غير مزخرف", "NON-DECORATIVE"), "translated attribute matches")


def test_plain_matching_unchanged() -> None:
    print("\n-- ordinary matching is untouched --")
    check(contains("قيمة العقد 2,680,000 درهم", "2,680,000"), "amount with separators")
    check(contains("قيمة العقد 2680000 درهم", "2,680,000"), "amount without separators")
    check(not contains("لا يوجد رقم", "2,680,000"), "absent amount is absent")
    check(contains("الإجراءات خلال 28 يومًا", "28"), "bare number matches")


def test_hallucination_criterion_unchanged() -> None:
    print("\n-- hallucination detection is not loosened --")
    corpus = load_corpus()
    check(
        hallucinated_numbers("صدر الحكم بتاريخ 31/12/2099", corpus) == ["31/12/2099"],
        "a date absent from the corpus is still flagged",
    )
    check(
        bool(hallucinated_numbers("بمبلغ 7,777,777 درهم", corpus)),
        "an amount absent from the corpus is still flagged",
    )
    check(
        hallucinated_numbers("قيمة العقد 2,680,000 درهم", corpus) == [],
        "a real amount is not flagged",
    )
    # The equivalence table must not become a back door into the corpus check.
    check(
        bool(hallucinated_numbers("صدرت في 31 ديسمبر 2099", corpus))
        or "31/12/2099" not in corpus,
        "spelling a date in words does not make an invented date acceptable",
    )


if __name__ == "__main__":
    test_date_equivalence()
    test_name_equivalence()
    test_plain_matching_unchanged()
    test_hallucination_criterion_unchanged()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("All evaluator checks passed.")
