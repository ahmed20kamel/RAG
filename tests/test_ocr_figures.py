"""Figures the Arabic OCR cut short are completed from the English reading — and only those.

Three cases measured on the case files, with the two readings Tesseract gave:

* «1,985.19 درهماً» read «9 درهماً» in Arabic; the English reading of the same box has
  the whole figure. Completed.
* «2,000,000 درهم» read whole in Arabic; the English reading of the same box made it
  «992,000,000» from the letters beside it. Left alone.
* «7/6/1981م» read «1»; the English reading gave «97/6/1981», a date with no day 97.
  Left alone.

    python tests/test_ocr_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import pytesseract  # noqa: E402

from app.parsers import ocr  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def readings(arabic: list[tuple], english: list[tuple]):
    """Stand-ins for Tesseract's word boxes: (text, left, top, width) on one line."""
    def table(words):
        return {
            "text": [w[0] for w in words], "left": [w[1] for w in words], "top": [w[2] for w in words],
            "width": [w[3] for w in words], "line_num": [1] * len(words), "block_num": [1] * len(words),
        }

    def fake(image, lang, output_type):
        return table(english if lang == "eng" else arabic)

    return fake


def complete(text: str, arabic: list[tuple], english: list[tuple]) -> str:
    original = pytesseract.image_to_data
    pytesseract.image_to_data = readings(arabic, english)
    try:
        return ocr._recover_numbers(None, text)
    finally:
        pytesseract.image_to_data = original


def main() -> int:
    text = "تجاه مالك المشروع بواقع\n9 درهماً يومياً اعتباراً من 14/06/2026"
    fixed = complete(
        text,
        arabic=[("9", 1288, 984, 155), ("درهماً", 1180, 984, 90), ("14/06/2026", 747, 984, 224)],
        english=[("1,985.19", 1288, 984, 153), ("14/06/2026", 747, 984, 224)],
    )
    check("1,985.19 درهماً" in fixed, "a figure the Arabic reading cut short is completed", fixed)

    text = "مبلغ إجمالي مقطوع وقدره 2,000,000 درهم"
    fixed = complete(
        text,
        arabic=[("2,000,000", 900, 300, 140), ("درهم", 820, 300, 60)],
        english=[("992,000,000", 895, 300, 148)],
    )
    check(fixed == text, "a figure read whole is never changed", fixed)

    text = "رقم 23 في 1م في شأن الشروط العامة"
    fixed = complete(
        text,
        arabic=[("1", 1300, 500, 120), ("في", 1250, 500, 30)],
        english=[("97/6/1981", 1296, 500, 126)],
    )
    check(fixed == text, "an impossible date is not taken", fixed)

    text = "1 يكون للطرف الثاني الحق في إنهاء العقد"
    fixed = complete(
        text,
        arabic=[("1", 1480, 200, 60), ("يكون", 1400, 200, 60)],
        english=[("600599", 1478, 200, 62)],
    )
    check(fixed == text, "a bare run of digits is not a completion", fixed)

    text = "بقيمة 5 درهم"
    fixed = complete(
        text,
        arabic=[("5", 700, 100, 20), ("درهم", 640, 100, 50)],
        english=[("135.50", 660, 100, 80)],
    )
    check(fixed == text, "a figure in a different box is not taken", fixed)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
