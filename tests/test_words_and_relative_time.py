"""Numbers written as words, and time stated relative to today.

Two gaps that were documented as things the system does not do, and are now read
deterministically. Each is tested twice: the reader alone, on its own traps, and then
at the point where it changes behaviour — the validator, the keyword index, ranking and
the prompt — because a reader that works in isolation and is never consulted fixes
nothing.

The traps matter more than the positive cases. Contracts are full of phrasing that looks
like these and is not: "عُشر قيمة العقد" is a fraction, "يوم الأحد" is a weekday, "في
اليوم" is a rate, and "قبل ثلاثين يومًا من انتهاء العقد" is a deadline. A reader that
fired on those would inject values and date ranges the documents never meant.

Deterministic and offline: no database, no model, no network.

Run: python tests/test_words_and_relative_time.py
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.number_words import digit_forms, spelled_numbers, with_digits  # noqa: E402
from app.core.retrieval import Candidate  # noqa: E402
from app.services.answer_validation import AnswerValidator  # noqa: E402
from app.services.keyword_index import KeywordIndex  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.rag_service import RagService  # noqa: E402
from app.services.relative_dates import read_relative  # noqa: E402
from app.services.reranking import W_EXPLICIT_DATE, FeatureReranker  # noqa: E402
from app.services.temporal import read_intent  # noqa: E402

FAILURES: list[str] = []
TODAY = date(2026, 9, 28)  # a Monday


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# 1. Reading numbers written as words
# ---------------------------------------------------------------------------


def numbers_are_read() -> None:
    print("\n-- 1. numbers written as words --")
    cases = {
        "تُقدَّم المطالبة خلال ثمانية وعشرين يوماً من الواقعة": [28],
        "مهلة الإخطار ثلاثون يومًا": [30],
        "غرامة قدرها خمسة و عشرون ألف درهم": [25000],
        "بلغت القيمة مليون وخمسمائة ألف درهم": [1500000],
        "ثلاثة آلاف وخمسمائة متر": [3500],
        "مئة وخمسة وعشرون بندًا": [125],
        "إحدى عشرة جلسة": [11],
        "اثنا عشر شهرًا": [12],
        "خلال يومين من الإشعار": [2],
        "ألفان وخمسمائة": [2500],
        "عشرون ألفًا": [20000],
        "خمس سنوات": [5],
        "within thirty days of notice": [30],
        "twenty-five thousand dirhams": [25000],
        "one hundred and twenty days": [120],
    }
    for text, wanted in cases.items():
        got = [n.value for n in spelled_numbers(text)]
        check(got == wanted, f"{wanted}: {text}", f"got {got}")


def words_that_are_not_numbers() -> None:
    print("\n-- 2. words that look like numbers and are not --")
    for text in (
        "عشر قيمة العقد",                        # one tenth, not ten
        "كل واحد من الأطراف",                    # a pronoun
        "جلسة يوم الأحد ويوم الاثنين والثلاثاء",  # weekdays
        "no one attended",
        "البند السابع من العقد",                 # an ordinal
    ):
        got = spelled_numbers(text)
        check(got == [], f"nothing read: {text}", str([(n.words, n.value) for n in got]))


def digit_views() -> None:
    print("\n-- 3. the two views callers use --")
    check(digit_forms("المهلة ثلاثون يومًا") == "30", "digit_forms appends the value")
    check(digit_forms("نص بلا أرقام مكتوبة") == "", "and is empty when there is none")
    check(
        "30 يوما" in with_digits("مهلة الإخطار ثلاثون يومًا"),
        "with_digits keeps the value beside the words that name it",
    )


# ---------------------------------------------------------------------------
# 2. Where it changes behaviour
# ---------------------------------------------------------------------------


def the_validator_accepts_a_value_stated_in_words() -> None:
    print("\n-- 4. the validator: a correct digit answer to a worded source --")
    # Values shorter than three characters are never checked, so the false alarm this
    # removes is on the figures that matter most: durations past 99 days and amounts.
    context = ("[1] يلتزم المقاول بإنجاز الأعمال خلال مئة وعشرين يومًا، "
               "وبدفع غرامة قدرها خمسة وعشرون ألف درهم عند الإخلال.")
    answer = "مدة الإنجاز 120 يومًا والغرامة 25,000 درهم [1]."

    bare = AnswerValidator._unsupported_values(answer, context, [])
    check(sorted(bare) == ["120", "25,000"],
          "reading the excerpts alone, both are flagged (the old false alarm)", str(bare))

    widened = AnswerValidator._unsupported_values(
        answer, RagService._verifiable(context, None), []
    )
    check(widened == [], "reading them as the pipeline now does, it is supported", str(widened))

    invented = AnswerValidator._unsupported_values(
        "مدة الإنجاز 150 يومًا [1].", RagService._verifiable(context, None), []
    )
    check(invented == ["150"], "a value the source does not state is still flagged", str(invented))


def the_keyword_index_matches_across_forms() -> None:
    print("\n-- 5. keyword search: digits find words, and words find digits --")
    index = KeywordIndex()

    class Row:
        def __init__(self, content: str) -> None:
            self.document_title, self.section, self.heading = "عقد", "المدد", "المدد"
            self.content = content

    index.build_from([
        ("worded", "d1", KeywordIndex._document_text(Row("مهلة الإخطار ثلاثون يومًا من الواقعة"))),
        ("three", "d1", KeywordIndex._document_text(Row("مهلة الإخطار ثلاثة أشهر للتجديد"))),
        ("digits", "d2", KeywordIndex._document_text(Row("مدة الضمان 12 شهرًا من الاستلام"))),
    ])
    hits = [chunk for chunk, _ in index.search("مهلة الإخطار 30 يومًا")]
    check(hits and hits[0] == "worded", "a question saying 30 ranks the clause saying ثلاثون first", str(hits))

    hits = [chunk for chunk, _ in index.search("مدة الضمان اثنا عشر شهرًا")]
    check(hits and hits[0] == "digits", "a question saying اثنا عشر finds the clause saying 12", str(hits))


# ---------------------------------------------------------------------------
# 3. Relative time
# ---------------------------------------------------------------------------


def relative_periods_are_resolved() -> None:
    print("\n-- 6. relative periods resolve against a stated today --")
    fmt = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731
    cases = {
        "ما الذي حدث الأسبوع الماضي؟": ("21/09/2026", "27/09/2026"),
        "إيه اللي حصل الأسبوع اللي فات": ("21/09/2026", "27/09/2026"),
        "ما المطلوب هذا الأسبوع؟": ("28/09/2026", "04/10/2026"),
        "ماذا تم الشهر الماضي؟": ("01/08/2026", "31/08/2026"),
        "ما الذي صدر أمس؟": ("27/09/2026", "27/09/2026"),
        "ماذا حدث أول أمس": ("26/09/2026", "26/09/2026"),
        "ما التطورات خلال آخر ثلاثين يومًا؟": ("29/08/2026", "28/09/2026"),
        "ما الذي حدث قبل ثلاثة أيام؟": ("25/09/2026", "25/09/2026"),
        "ما الذي تغير منذ أسبوعين؟": ("14/09/2026", "28/09/2026"),
        "ما الإنجازات في العام الماضي": ("01/01/2025", "31/12/2025"),
        "what happened last week?": ("21/09/2026", "27/09/2026"),
        "changes in the past 10 days": ("18/09/2026", "28/09/2026"),
    }
    for question, (start, end) in cases.items():
        window = read_relative(question, TODAY)
        got = (fmt(window.start), fmt(window.end)) if window else None
        check(got == (start, end), f"{start}–{end}: {question}", f"got {got}")


def phrasing_that_is_not_a_period() -> None:
    print("\n-- 7. contract phrasing that must never become a date window --")
    for question in (
        "ما قيمة غرامة التأخير في اليوم؟",               # a rate
        "ما الوضع اليوم؟",                               # "currently" — the latest reader's job
        "ما الغرامة عن التأخير لأكثر من ثلاثين يومًا؟",  # a threshold
        "يجب الإخطار قبل ثلاثين يومًا من انتهاء العقد",  # a deadline from an event
        "ما آخر مرحلة وصلت إليها القضية؟",               # "latest", no count
        "خلال 30 يوما من تاريخ الإشعار",                 # a deadline
        "ما البند السابق في العقد؟",
        "notice must be given 30 days before expiry",
    ):
        check(read_relative(question, TODAY) is None, f"no window: {question}")


def an_explicit_date_still_wins() -> None:
    print("\n-- 8. a named date keeps precedence over a relative one --")
    intent = read_intent("ما الذي حدث في 13/08/2026 وليس الأسبوع الماضي؟")
    check(bool(intent.explicit_dates) and intent.window is None, "the named date is used")


def ranking_rewards_the_period() -> None:
    print("\n-- 9. ranking: inside the period earns what a named date earns --")
    analysis = QueryAnalyzer().analyze("ما الذي حدث في الجلسات الأسبوع الماضي؟")
    window = analysis.temporal.window
    check(window is not None, "the analyser carries the window")
    if window is None:
        return
    check(analysis.needs_dated_sweep, "and asks for the dated sweep")

    def candidate(chunk_id: str, section: str) -> Candidate:
        return Candidate(
            chunk_id=chunk_id, document_id="d1", document_title="الملف", filename="f.md",
            category="General", section=section, section_id=chunk_id,
            heading=section.split("→")[-1].strip(),
            parent_section="", content="عُقدت الجلسة وقُدمت المذكرات.",
            vector_score=0.5, fused_score=0.02,
        )

    inside = window.start.strftime("%d/%m/%Y")
    before = date(window.start.year, window.start.month, 1).strftime("%d/%m/%Y")
    pool = [
        candidate("older", f"الملف → جلسة ({before})"),
        candidate("inside", f"الملف → جلسة ({inside})"),
    ]
    ranked = FeatureReranker().rerank(analysis, pool, limit=2)
    check(ranked[0].chunk_id == "inside", "the session inside the period ranks first",
          str([c.chunk_id for c in ranked]))
    check(abs(ranked[0].recency_boost - W_EXPLICIT_DATE) < 1e-9, "with the named-date weight")


def the_prompt_states_the_period() -> None:
    print("\n-- 10. the prompt: today's date and the period, stated --")
    window = read_relative("ما الذي حدث الأسبوع الماضي؟", TODAY)
    block = window.prompt_block()
    check("تاريخ اليوم: 28/09/2026" in block, "today's date is stated")
    check("من 21/09/2026 إلى 27/09/2026" in block, "and so is the period")

    widened = RagService._verifiable("[1] جلسة 24/09/2026", window)
    check("21/09/2026" in widened and "28/09/2026" in widened,
          "and an answer repeating those dates is not flagged as unsupported")


def main() -> None:
    print("number words and relative time")
    numbers_are_read()
    words_that_are_not_numbers()
    digit_views()
    the_validator_accepts_a_value_stated_in_words()
    the_keyword_index_matches_across_forms()
    relative_periods_are_resolved()
    phrasing_that_is_not_a_period()
    an_explicit_date_still_wins()
    ranking_rewards_the_period()
    the_prompt_states_the_period()

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("numbers in words and relative time are read, and the traps are not")


if __name__ == "__main__":
    main()
