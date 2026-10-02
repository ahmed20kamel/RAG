"""Time-aware ranking: when newer evidence should win, and when it must not.

The failure this suite exists to prevent was measured, not imagined. Asked for the
latest stage of a matter, the system answered from evidence that stopped five days
before the decisive ruling — and said so with the same confidence it uses to quote a
clause. The ruling was indexed and reachable; it simply never outranked the older
sections, because nothing in the ranking knew that one date is later than another.

Half of what follows is the opposite check. A system that always prefers newer evidence
has not learned about time, it has learned a bias: it would answer "what was originally
agreed?" with the latest amendment, which is a new wrong answer traded for an old one.
So the cases below are balanced on purpose — latest, earliest, explicit dates, and
questions with no temporal component at all, which must rank exactly as they did before.

Deterministic and offline: no database, no model, no network.

Run: python tests/test_temporal_retrieval.py
"""

from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.retrieval import Candidate  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.reranking import FeatureReranker  # noqa: E402
from app.services.temporal import (  # noqa: E402
    evidence_date,
    read_intent,
    recency_weight,
)

FAILURES: list[str] = []
ANALYZER = QueryAnalyzer()
RERANKER = FeatureReranker()


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def candidate(
    chunk_id: str,
    section: str,
    content: str,
    *,
    vector: float = 0.60,
    fused: float = 0.02,
) -> Candidate:
    """A retrieved passage with the scores the earlier arms would have given it.

    The similarity is held equal across a case's candidates unless a test varies it on
    purpose, so that what separates them is the signal under test and not an accident of
    how the fixture was written.
    """
    return Candidate(
        chunk_id=chunk_id,
        document_id="d1",
        filename="matter.md",
        document_title="ملف",
        section=section,
        section_id=chunk_id,
        heading=section.split("→")[-1].strip(),
        parent_section="",
        content=content,
        vector_score=vector,
        fused_score=fused,
    )


def order(question: str, pool: list[Candidate]) -> list[str]:
    analysis = ANALYZER.analyze(question)
    ranked = RERANKER.rerank(analysis, list(pool), limit=len(pool))
    return [c.chunk_id for c in ranked]


# ---------------------------------------------------------------------------
# 1. Reading time out of the question
# ---------------------------------------------------------------------------


def intent_detection() -> None:
    print("\n-- 1. what the question says about time --")

    latest = [
        "ما آخر مرحلة وصلت إليها القضية؟",
        "ما أحدث قرار معتمد؟",
        "ما الوضع الحالي للمشروع؟",
        "أين وصلنا الآن؟",
        "ما آخر تحديث لدينا؟",
        "what is the latest stage?",
        "what is the current status of the claim?",
        "give me the most recent decision",
        "where do we stand so far?",
    ]
    for question in latest:
        check(read_intent(question).wants_latest, f"latest: {question}")

    earliest = [
        "ما الاتفاق الأصلي في بداية المشروع؟",
        "ما كان التقدير المبدئي؟",
        "what was initially agreed?",
        "what was the original scope?",
    ]
    for question in earliest:
        intent = read_intent(question)
        check(intent.wants_earliest and not intent.wants_latest, f"earliest: {question}")

    # An explicit date is a better statement of intent than any cue list can infer, so
    # it switches the generic preference off rather than adding to it.
    for question in ("ماذا حدث بتاريخ 13/08/2026؟", "ما قرار 19 يوليو 2026؟"):
        intent = read_intent(question)
        check(bool(intent.explicit_dates), f"explicit date read: {question}")
        check(not intent.wants_latest, f"and no generic recency preference: {question}")

    neutral = [
        "ما إجراءات المطالبات وفق عقد FIDIC؟",
        "من هو الطرف الآخر في العقد؟",          # الآخر — the other, not the last
        "ما سياسة الحضور والانصراف؟",           # الانصراف contains الان
        "ما قيمة غرامة التأخير؟",
        "what does the method statement require?",
    ]
    for question in neutral:
        check(not read_intent(question).is_temporal, f"no temporal reading: {question}")


# ---------------------------------------------------------------------------
# 2. Reading dates out of the evidence
# ---------------------------------------------------------------------------


def evidence_dates() -> None:
    print("\n-- 2. the date a passage is about --")

    check(
        evidence_date("الحكم القطعي (13/08/2026)", "", "") == (2026, 8, 13),
        "a date in the heading is read",
    )
    check(
        evidence_date("", "", "صدر القرار في 19 يوليو 2026 بعد الجلسة") == (2026, 7, 19),
        "a date written in words is read",
    )
    check(evidence_date("قسم بلا تاريخ", "", "نص عادي") is None, "an undated passage reads as None")

    # A section describing an event usually also cites the older papers leading to it.
    # The section is about the event, so the newest date in it wins.
    check(
        evidence_date("", "", "بناءً على كتاب 02/03/2026 وتقرير 08/08/2026 صدر القرار")
        == (2026, 8, 8),
        "among several dates, the newest is taken",
    )

    # A hearing scheduled for next year is not evidence of what has happened.
    future = (date.today() + timedelta(days=400)).strftime("%d/%m/%Y")
    check(
        evidence_date("", "", f"الجلسة القادمة {future} وصدر القرار 01/01/2026")
        == (2026, 1, 1),
        "a future date is discarded rather than treated as the latest",
    )

    check(recency_weight((2026, 8, 13), (2026, 8, 13)) == 1.0, "the newest scores full weight")
    check(
        0.0 < recency_weight((2026, 7, 23), (2026, 8, 13)) < 1.0,
        "three weeks older scores less, but not zero",
    )
    check(recency_weight(None, (2026, 8, 13)) == 0.0, "an undated passage earns nothing")


# ---------------------------------------------------------------------------
# 3. Ranking — the behaviour that actually failed
# ---------------------------------------------------------------------------


def latest_wins_when_asked() -> None:
    print("\n-- 3. asked for the latest, the newest dated evidence ranks first --")

    pool = [
        candidate("old", "المسار → الجلسة الخامسة (23/07/2026)",
                  "انعقدت الجلسة وقُدمت المذكرة التكميلية الثالثة بشأن القضية."),
        candidate("mid", "المسار → التقرير النهائي (08/08/2026)",
                  "أُودع التقرير النهائي للخبير بشأن القضية في أربع وعشرين صفحة."),
        candidate("new", "المسار → الحكم القطعي (13/08/2026)",
                  "حكمت المحكمة بانتهاء الدعوى بشأن القضية وفقاً لتقرير الخبرة."),
    ]
    ranked = order("ما آخر مرحلة وصلت إليها القضية؟", pool)
    check(ranked[0] == "new", "the newest section ranks first", str(ranked))

    # The same pool, asked the same thing in English.
    ranked = order("what is the latest stage of the case?", pool)
    check(ranked[0] == "new", "and the same holds in English", str(ranked))


def historical_questions_are_not_dragged_forward() -> None:
    print("\n-- 4. asked what came first, the oldest evidence ranks first --")

    pool = [
        candidate("first", "المسار → الاتفاق المبدئي (05/01/2026)",
                  "اتُفق مبدئياً على نطاق الأعمال وقيمة العقد بين الطرفين."),
        candidate("later", "المسار → الملحق الثاني (30/06/2026)",
                  "عُدّل نطاق الأعمال وقيمة العقد بين الطرفين بموجب الملحق."),
    ]
    ranked = order("ما الاتفاق الأصلي في بداية المشروع؟", pool)
    check(ranked[0] == "first", "the earliest section ranks first", str(ranked))

    # And the mirror image, to prove the signal is directional rather than a fixed bias.
    ranked = order("ما آخر تعديل على نطاق الأعمال؟", pool)
    check(ranked[0] == "later", "while 'latest' on the same pool reverses it", str(ranked))


def explicit_date_beats_recency() -> None:
    print("\n-- 5. a named date wins over the generic preference --")

    pool = [
        candidate("named", "المسار → جلسة (19/07/2026)",
                  "صدر في هذا التاريخ قرار بشأن ندب الخبير وتحديد المأمورية."),
        candidate("newest", "المسار → الحكم (13/08/2026)",
                  "صدر الحكم القطعي بانتهاء الدعوى بشأن المأمورية والخبرة."),
    ]
    ranked = order("ماذا تقرر بتاريخ 19/07/2026؟", pool)
    check(
        ranked[0] == "named",
        "the section carrying the named date ranks first, not the newest one",
        str(ranked),
    )


def relevance_still_outranks_recency() -> None:
    print("\n-- 6. a recent but irrelevant passage does not overtake a relevant one --")

    pool = [
        candidate(
            "relevant-old",
            "المسار → غرامة التأخير (05/02/2026)",
            "قيمة غرامة التأخير القصوى للمشروع محددة في العقد بنسبة عشرة بالمئة.",
            vector=0.82,
        ),
        candidate(
            "recent-unrelated",
            "المسار → لوحات السلامة (20/08/2026)",
            "رُكّبت لوحات إرشادية جديدة عند مدخل الموقع وتم تحديث ألوانها.",
            vector=0.31,
        ),
    ]
    ranked = order("ما آخر وضع لغرامة التأخير القصوى؟", pool)
    check(
        ranked[0] == "relevant-old",
        "the passage that answers the question stays first despite being older",
        str(ranked),
    )


def undated_evidence_is_not_punished() -> None:
    print("\n-- 7. a passage with no date is ranked on its merits, not pushed down --")

    pool = [
        candidate(
            "undated-relevant",
            "المسار → التزامات المقاول",
            "يلتزم المقاول بإخطار المهندس خلال ثمانية وعشرين يوماً من واقعة التأخير.",
            vector=0.88,
        ),
        candidate(
            "dated-vague",
            "المسار → محضر (11/08/2026)",
            "عُقد اجتماع تنسيقي قصير لمتابعة الأعمال العامة في الموقع.",
            vector=0.34,
        ),
    ]
    ranked = order("ما آخر ما لدينا عن التزام المقاول بالإخطار؟", pool)
    check(
        ranked[0] == "undated-relevant",
        "the undated passage that answers the question still ranks first",
        str(ranked),
    )


def neutral_questions_are_unaffected() -> None:
    print("\n-- 8. a question with no temporal component ranks exactly as before --")

    pool = [
        candidate("a", "المسار → المطالبات (01/02/2026)",
                  "تُقدَّم المطالبة خلال ثمانية وعشرين يوماً من الواقعة وفق البند عشرين."),
        candidate("b", "المسار → السلامة (30/08/2026)",
                  "تُركّب حواجز الحماية حول الحفر العميق قبل بدء أعمال التدعيم."),
    ]
    analysis = ANALYZER.analyze("ما إجراءات تقديم المطالبة؟")
    ranked = RERANKER.rerank(analysis, list(pool), limit=2)
    check(not analysis.temporal.is_temporal, "the question reads as non-temporal")
    check(
        all(c.recency_boost == 0.0 for c in ranked),
        "no recency adjustment is applied to any candidate",
        str([(c.chunk_id, c.recency_boost) for c in ranked]),
    )
    check(ranked[0].chunk_id == "a", "and the relevant passage still wins", str([c.chunk_id for c in ranked]))


def adjustments_are_explainable() -> None:
    print("\n-- 9. every adjustment can be accounted for --")

    pool = [
        candidate("new", "المسار → الحكم (13/08/2026)", "صدر الحكم بشأن القضية."),
        candidate("old", "المسار → جلسة (23/07/2026)", "انعقدت الجلسة بشأن القضية."),
    ]
    analysis = ANALYZER.analyze("ما آخر مرحلة في القضية؟")
    ranked = RERANKER.rerank(analysis, list(pool), limit=2)
    top = ranked[0]
    check(top.recency_boost > 0, "the boost is recorded as a number, not folded away")
    check(bool(top.rank_notes), "a note explains it", str(top.rank_notes))
    check(
        top.date_label == "13/08/2026",
        "and names the date it was based on",
        top.date_label,
    )
    check(
        any(analysis.temporal.cue in note for note in top.rank_notes),
        "the note quotes the cue from the question",
        str(top.rank_notes),
    )


def multiple_dates_in_one_document() -> None:
    print("\n-- 10. several dated sections of one document sort among themselves --")

    pool = [
        candidate(f"s{i}", f"المسار → مرحلة ({d})", f"وقائع المرحلة بشأن المشروع رقم {i}.")
        for i, d in enumerate(
            ["05/01/2026", "14/03/2026", "23/07/2026", "08/08/2026", "13/08/2026"]
        )
    ]
    ranked = order("ما أحدث مرحلة في المشروع؟", pool)
    check(ranked[0] == "s4", "the newest of five dated sections ranks first", str(ranked))
    check(ranked[-1] == "s0", "and the oldest ranks last", str(ranked))

    ranked = order("ما أول مرحلة في المشروع؟", pool)
    check(ranked[0] == "s0", "asked for the first, the order reverses", str(ranked))


def main() -> None:
    print("temporal retrieval")
    intent_detection()
    evidence_dates()
    latest_wins_when_asked()
    historical_questions_are_not_dragged_forward()
    explicit_date_beats_recency()
    relevance_still_outranks_recency()
    undated_evidence_is_not_punished()
    neutral_questions_are_unaffected()
    adjustments_are_explainable()
    multiple_dates_in_one_document()

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("newer evidence wins only where the question asked for it")


if __name__ == "__main__":
    main()
