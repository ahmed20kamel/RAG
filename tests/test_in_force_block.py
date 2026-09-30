"""Lifting a document's own statement of which version counts into the prompt.

Ranking could not decide this, and the measurements said so twice. Demoting superseded
passages worked for the question it was built for and, loose enough to catch it, marked
half a document as superseded — a page of standing reminders shares six content words
with almost everything in the same file. Tightened until it was safe, it no longer
caught the case it existed for.

So this does not touch ranking. When a document states plainly that a value is the one
in force — "التكليف النافذة = 3 بنود فقط" — that sentence is quoted into the prompt
above the sources, and arrives whether or not the passage carrying it ranked first.

Two properties this suite exists to hold, and they matter more than the feature working:

**It quotes, never paraphrases.** Every line must be a span of the source text. A block
that summarised could introduce a claim the documents never made, which is worse than
the ambiguity it was built to remove.

**It stays silent unless a document is explicit.** No cue, no block. For almost every
question this produces nothing and the prompt is byte-identical to what it was.

Deterministic and offline: no database, no model, no network.

Run: python tests/test_in_force_block.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.services.in_force import MAX_LINES, extract  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def source(citation: int, excerpt: str, filename: str = "matter.md") -> object:
    class _Source:
        def __init__(self) -> None:
            self.citation = citation
            self.excerpt = excerpt
            self.filename = filename

    return _Source()


# ---------------------------------------------------------------------------
# 1. What it lifts
# ---------------------------------------------------------------------------


def it_lifts_currency_statements() -> None:
    print("\n-- 1. a document stating which version is in force --")

    result = extract([
        source(1, "بعد قراءة النص الفعلي للحكم المصحح، التكليف النافذة = 3 بنود فقط."),
        source(2, "| ~~4~~ | المواد المشونة والمعدات | ❌ **محذوف** |"),
    ])
    check(len(result.statements) == 2, f"كلا العبارتين مُستخرَجة ({len(result.statements)})")

    rendered = result.render()
    check("3 بنود فقط" in rendered, "القيمة السارية تظهر في الكتلة", rendered[:160])
    check("[1]" in rendered and "[2]" in rendered, "مع استشهاد كل عبارة")
    check("النسخة النافذة" in rendered, "والكتلة تعرّف نفسها")

    # The deletion follows the statement it qualifies, never the other way round.
    order = [s.kind for s in result.statements]
    check(order == ["in_force", "deletion"], "السارية أولاً ثم المحذوف", str(order))


def it_quotes_rather_than_summarises() -> None:
    print("\n-- 2. every line is a span of the source, not a summary --")

    excerpt = "التكليف النافذة الرسمية = 3 بنود فقط بعد التصحيح."
    result = extract([source(1, excerpt)])
    check(bool(result.statements), "استُخرجت عبارة")
    if result.statements:
        line = result.statements[0].text
        check(line in excerpt, "النص منقول حرفيًا من المصدر", f"{line!r}")


# ---------------------------------------------------------------------------
# 2. When it stays quiet — the property that protects every other question
# ---------------------------------------------------------------------------


def it_stays_silent_without_a_cue() -> None:
    print("\n-- 3. ordinary evidence produces nothing at all --")

    quiet = [
        "تُقدَّم المطالبة خلال ثمانية وعشرين يوماً من الواقعة وفق البند عشرين.",
        "قيمة العقد الإجمالية 1,450,000 درهم كما اعتُمدت في الملحق الأول.",
        "تُركّب حواجز الحماية حول الحفر العميق قبل بدء أعمال التدعيم.",
        "The maximum unsupported excavation depth in loose sand is 1.5 m.",
        "عُقدت الجلسة الخامسة وقُدمت المذكرة التكميلية الثالثة.",
    ]
    for text in quiet:
        result = extract([source(1, text)])
        check(
            result.render() == "",
            f"لا كتلة: {text[:48]}",
            str([s.text for s in result.statements]),
        )


def a_declaration_without_a_value_is_not_lifted() -> None:
    print("\n-- 4. a claim of currency with nothing in it is not a claim --")

    # "النسخة النافذة معتمدة" tells a reader nothing they can act on. Quoting it would
    # add words to the prompt without adding information, and every word in this block
    # is taken from the space the evidence needs.
    result = extract([source(1, "النسخة النافذة معتمدة من الإدارة ومطبقة على الجميع.")])
    check(result.render() == "", "بلا قيمة رقمية ⇒ لا تُستخرَج", str(result.statements))


def it_never_floods_the_prompt() -> None:
    print("\n-- 5. a file using the words in passing cannot take over the prompt --")

    many = [
        source(i, f"البند {i} من التكليف النافذة رقم {i} محذوف بعد التصحيح.")
        for i in range(1, 20)
    ]
    result = extract(many)
    check(
        len(result.statements) <= MAX_LINES,
        f"العدد مقتصّ عند {MAX_LINES} ({len(result.statements)})",
    )
    # Truncated, not discarded: dropping the whole block once it grows past the cap
    # would throw away the lines that settle the question because other passages use
    # the word in passing.
    check(bool(result.statements), "ومع ذلك الكتلة ليست فارغة")


def duplicates_are_collapsed() -> None:
    print("\n-- 6. the same sentence in three passages is quoted once --")

    line = "التكليف النافذة = 3 بنود فقط بعد التصحيح."
    result = extract([source(1, line), source(2, line), source(3, line)])
    check(len(result.statements) == 1, f"مرة واحدة ({len(result.statements)})")


def it_reads_english_too() -> None:
    print("\n-- 7. the same in English --")

    result = extract([
        source(1, "The scope in force after correction contains 3 items only."),
        source(2, "Item 4 was deleted from the scope."),
    ])
    check(len(result.statements) == 2, f"كلاهما ({len(result.statements)})")


def main() -> None:
    print("in-force block")
    it_lifts_currency_statements()
    it_quotes_rather_than_summarises()
    it_stays_silent_without_a_cue()
    a_declaration_without_a_value_is_not_lifted()
    it_never_floods_the_prompt()
    duplicates_are_collapsed()
    it_reads_english_too()

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("the document's own statement of currency reaches the model, and nothing else does")


if __name__ == "__main__":
    main()
