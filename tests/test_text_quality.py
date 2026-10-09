"""A damaged PDF text layer is recognised, and a clean one is left alone.

The three kinds of damage measured on the case files — letters split apart, foreign
symbols in place of Arabic letters, and "لا" stored reversed — each push a page over the
line where it is re-read as an image; ordinary Arabic, English and figures do not.

Offline. Run: python tests/test_text_quality.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from collections import Counter  # noqa: E402

from app.parsers.text_quality import DAMAGED, SWAPPED, damage, swapped  # noqa: E402
from app.parsers.text_repair import logical_grid, unswap_letters, visual_to_logical  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


CLEAN = ("بصفتنا الاستشاري المعتمد والمشرف على أعمال المشروع، وبناءً على المعاينات الميدانية التي أجراها "
         "مكتبنا قبل وبعد واقعة الانهيار، ومراجعة سجلات المشروع ومحاضر الاستلام والتسليم المعتمدة، فقد تم "
         "إعداد هذا التقرير الفني الموحد لتوثيق الحالة الفنية للأعمال. قيمة العقد 2,680,000 درهم.")
SPLIT = ("بيا ن ا لأثر ا لزم ۚي ا لمباشر للو ا قعة ع ڴ ʄ س ؈ف ا لمشر وع و ȋرنانجھ ا لزم ۚي و تحديد مد ة "
         "ا لتعطيل ا لناشئة ع ٔڈ ا و حصر ما ترتب أو قد ي ؅ف تب عل ٕڈ ا من غر ا ما ت تأخ ؈ف تتحمل ه ا ا لمدعية")
REVERSED = ("بصفتنا االستشاري المعتمد والمشرف على أعمال المشروع، وبناءً على المعاينات الميدانية التي أجراها "
            "مكتبنا قبل وبعد واقعة االنهيار، ومراجعة سجالت المشروع ومحاضر االستالم والتسليم المعتمدة، فقد تم "
            "إعداد هذا التقرير الفني الموحد لتوثيق الحالة الفنية لألعمال واألضرار.")
ENGLISH = "COMMERCIAL CLAIM FORM — Claimant Details. Contract Reference Number FAB 00240/2024, AED 2,680,000. " * 3


def main() -> int:
    check(damage(CLEAN) < DAMAGED, "clean Arabic is left alone", str(damage(CLEAN)))
    check(damage(SPLIT) >= DAMAGED, "letters split apart with foreign symbols are damage", str(damage(SPLIT)))
    check(damage(REVERSED) >= DAMAGED, "a reversed «لا» is damage", str(damage(REVERSED)))
    check(damage(ENGLISH) == 0.0, "English and figures are not judged", str(damage(ENGLISH)))
    check(damage("قصير") == 0.0, "too little text to judge is not damage")

    # A ligature font swaps lam with the next letter (the Safcom memo on the final report).
    memo = ("عناية حمكمة ابوظبي التجارية االبتدائي املوقرة مذكرة تعقيبية علي تقرير اخلربة النهائي "
            "مقدمة من املدعي عليها ضد املدعي ويمثلها قانونا املدير العام وحيث ان اخلبري املنتدب "
            "قد انتهى في تقريره الى ان سبب االنهيار هو عدم التاسيس على املنسوب الصحيح")
    check(swapped(memo) >= SWAPPED, "an article with its lam swapped is detected", str(swapped(memo)))
    check(swapped(CLEAN) < SWAPPED, "clean Arabic shows no swapped letters", str(swapped(CLEAN)))
    vocabulary = Counter("محكمة الموقرة الخبرة المدعي الخبير".split())
    fixed = unswap_letters(memo, vocabulary)
    check("محكمة" in fixed and "الموقرة" in fixed and "الخبرة" in fixed and "المدعي عليها" in fixed,
          "swapped letters are put back from the document's own words", fixed[:90])
    check(unswap_letters("قرأ الطالب الدرس", vocabulary) == "قرأ الطالب الدرس",
          "a word the vocabulary does not explain is left as it is")

    # Table cells stored in drawing order (the expert reports and the delay schedule).
    check(visual_to_logical("ن؈هاش دمحأ دمحم دمحم ماصع") == "عصام محمد محمد أحمد شاهين",
          "a reversed name reads right", visual_to_logical("ن؈هاش دمحأ دمحم دمحم ماصع"))
    check(visual_to_logical("موي/مهرد 1,985.19") == "1,985.19 درهم/يوم",
          "figures keep their digits when the line is turned", visual_to_logical("موي/مهرد 1,985.19"))
    reversed_grid = [["م", "دنبلا", "ةدحولا"], ["1", "رادج ةنايص", "ةيمكلا"], ["Name", "ن؈هاش دمحأ", "ةمارغلا"]]
    turned = logical_grid(reversed_grid)
    check(turned[0][1] == "البند" and turned[2][1] == "أحمد شاهين" and turned[2][0] == "Name",
          "a reversed table is turned, names and Latin cells included", str(turned))
    upright = [["البند", "الوحدة", "الكمية"], ["صيانة جدار", "متر", "35"]]
    check(logical_grid(upright) == upright, "a table already in reading order is left alone")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
