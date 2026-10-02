"""Which version of a fact wins when a document holds more than one.

The measured failure: one question, three answers living side by side in the same file,
and three different phrasings of the question returned three different numbers.

    "كم بندًا في المأمورية النافذة؟"      → 3   ✅ (the phrasing happened to say النافذة)
    "ما بنود مأمورية الخبير؟"             → 7   ❌
    "كم بندًا كلّف الحكمُ الخبيرَ به؟"     → 5   ❌

All three passages were retrieved every time. The model answered with whichever ranked
first, and nothing in the ranking knew that one of them had been superseded by the
others.

Dates cannot settle this — the competing passages carry none. What settles it is that
the correcting passage says so in words. This suite holds that reading to a standard:
it must fire on a passage that declares its own standing, and must stay silent on the
overwhelming majority that do not, because a signal that fires everywhere is a bias.

Deterministic and offline: no database, no model, no network.

Run: python tests/test_supersession.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.retrieval import Candidate  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.reranking import FeatureReranker  # noqa: E402
from app.services.supersession import read_status  # noqa: E402

FAILURES: list[str] = []
ANALYZER = QueryAnalyzer()
RERANKER = FeatureReranker()


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def candidate(chunk_id: str, section: str, content: str, *, vector: float = 0.66) -> Candidate:
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
        fused_score=0.02,
    )


def order(question: str, pool: list[Candidate]) -> list[str]:
    analysis = ANALYZER.analyze(question)
    return [c.chunk_id for c in RERANKER.rerank(analysis, list(pool), limit=len(pool))]


# ---------------------------------------------------------------------------
# 1. Reading a passage's own standing
# ---------------------------------------------------------------------------


def standing_is_read() -> None:
    print("\n-- 1. what a passage says about itself --")

    current = [
        ("🔴 قراءة نهائية دقيقة للحكم المصحح — تصحيحاً لخطأ سابق", ""),
        ("🔔 التذكيرات الدائمة", "المأمورية النافذة الرسمية = 3 بنود فقط."),
        ("المأمورية", "الصيغة النافذة بعد التصحيح تشمل ثلاثة بنود."),
        ("Scope", "This is the corrected version, in force from the ruling."),
        ("Schedule", "Final version — supersedes the earlier annex."),
    ]
    for section, content in current:
        status = read_status(section, "", content)
        check(status.is_current, f"في السريان: {section[:46]}", f"cue={status.cue!r}")

    superseded = [
        ("مأمورية الخبير", "نسخة قديمة قبل التصحيح — لم تعد سارية."),
        ("الملحق الأول", "هذا البند ملغى بموجب الملحق الثاني."),
        ("Annex A", "Superseded by Annex B."),
        ("Rates", "Previous version — no longer in force."),
    ]
    for section, content in superseded:
        status = read_status(section, "", content)
        check(status.is_superseded, f"ملغاة: {section[:46]}", f"cue={status.cue!r}")

    # The ordinary case, and the one that matters most: silence.
    neutral = [
        ("إجراءات المطالبات وإدارة التأخير", "تُقدَّم المطالبة خلال ثمانية وعشرين يوماً."),
        ("مأمورية الخبير (5 بنود)", "بيان الأعمال ونسبتها، والمطابقة، والمخالفات."),
        ("Method statement", "Shoring is installed before excavation begins."),
        ("جدول المدفوعات", "الدفعة الأولى عند التوقيع والثانية عند التسليم."),
        ("التسلسل الزمني", "عُقدت الجلسة وقُدمت المذكرة التكميلية."),
    ]
    for section, content in neutral:
        status = read_status(section, "", content)
        check(
            not status.state,
            f"لا يدّعي شيئًا: {section[:44]}",
            f"fired on {status.cue!r}",
        )


def a_correction_is_not_a_casualty() -> None:
    print("\n-- 2. the passage announcing a deletion is the authority, not the victim --")

    # "محذوف" lives inside the correcting passage. Reading it as a retirement marker
    # would push down the one passage that settles the question — the exact inversion
    # this rule exists to avoid.
    status = read_status(
        "🔴 قراءة نهائية دقيقة للحكم المصحح",
        "",
        "المأمورية النافذة = 3 بنود فقط. ~~4~~ المواد المشونة ❌ محذوف. ~~5~~ الكفالات ❌ محذوف.",
    )
    check(status.is_current, "الجدول الذي يعلن الحذف يُقرأ كسارٍ", str(status))
    check(not status.is_superseded, "ولا يُقرأ كملغى")


# ---------------------------------------------------------------------------
# 3. Ranking — the behaviour that actually failed
# ---------------------------------------------------------------------------


EFFECTIVE = candidate(
    "effective",
    "الملف → 🔴 قراءة نهائية دقيقة للحكم المصحح 16/07 — تصحيحاً لخطأ سابق",
    "المأمورية النافذة = 3 بنود فقط: بيان الأعمال ونسبتها، والمطابقة للمخططات، "
    "وتحديد المخالفات وعيوب التنفيذ. البند 4 والبند 5 محذوفان.",
)
EARLIER = candidate(
    "earlier",
    "الملف → مأمورية الخبير (5 بنود)",
    "بيان الأعمال ونسبتها، والمطابقة للمخططات والمواصفات، وتحديد المخالفات، "
    "والمواد المشونة والمعدات، والكفالات والضمانات المالية.",
)
ORIGINAL = candidate(
    "original",
    "الملف → طلبات صحيفة الدعوى",
    "المطلوب من الخبير: إثبات حالة السور، وبيان الأعمال، وأسباب الانهيار، "
    "وحصر الأضرار، والأثر الزمني، وسماع الأطراف، والانتقال للبلدية.",
)


def the_version_in_force_wins() -> None:
    print("\n-- 3. the version in force ranks first, whatever the wording --")

    pool = [ORIGINAL, EARLIER, EFFECTIVE]
    # The three phrasings that produced three different numbers. None of the last two
    # contains a word that disambiguates the version; that is the point.
    for question in (
        "كم بندًا في المأمورية النافذة للخبير؟",
        "ما بنود مأمورية الخبير؟",
        "كم بندًا كلّف الحكمُ الخبيرَ به؟",
        "what is the expert's scope of work?",
    ):
        ranked = order(question, pool)
        # The property under test is the relation, not the absolute position: the
        # version that was replaced must not outrank the one that replaced it. Asserting
        # first place instead would make the test depend on how the other candidates
        # happen to score, which is not what this signal decides.
        check(
            ranked.index("effective") < ranked.index("earlier"),
            f"السارية قبل المتجاوَزة: {question[:48]}",
            str(ranked),
        )


def retired_passages_sink() -> None:
    print("\n-- 4. a passage marked as retired ranks below its replacement --")

    retired = candidate(
        "retired",
        "الملف → جدول الأسعار — نسخة قديمة",
        "هذه النسخة ملغاة ولم تعد سارية. سعر المتر المكعب 180 درهماً.",
    )
    live = candidate(
        "live",
        "الملف → جدول الأسعار",
        "سعر المتر المكعب 215 درهماً للخرسانة المسلحة حسب العقد.",
    )
    ranked = order("ما سعر المتر المكعب؟", [retired, live])
    check(ranked[0] == "live", "النسخة القائمة أولاً", str(ranked))


def ordinary_questions_are_untouched() -> None:
    print("\n-- 5. passages that claim nothing are ranked exactly as before --")

    pool = [
        candidate("a", "الملف → المطالبات", "تُقدَّم المطالبة خلال ثمانية وعشرين يوماً من الواقعة."),
        candidate("b", "الملف → السلامة", "تُركّب حواجز الحماية حول الحفر قبل التدعيم."),
    ]
    analysis = ANALYZER.analyze("ما مهلة تقديم المطالبة؟")
    ranked = RERANKER.rerank(analysis, list(pool), limit=2)
    check(
        all(c.standing_adjustment == 0.0 for c in ranked),
        "لا تعديل على أي مرشّح",
        str([(c.chunk_id, c.standing_adjustment) for c in ranked]),
    )
    check(ranked[0].chunk_id == "a", "والمقطع المناسب لا يزال أولاً", str([c.chunk_id for c in ranked]))


def relevance_still_outranks_standing() -> None:
    print("\n-- 6. a passage in force but about something else does not jump the queue --")

    pool = [
        candidate(
            "relevant",
            "الملف → غرامة التأخير",
            "قيمة غرامة التأخير اليومية 1,985.19 درهماً بحد أقصى مئتين وثمانية وستين ألفاً.",
            vector=0.90,
        ),
        candidate(
            "in-force-elsewhere",
            "الملف → 🔔 التذكيرات الدائمة",
            "النسخة النافذة من دليل السلامة هي الثالثة بعد التصحيح.",
            vector=0.22,
        ),
    ]
    ranked = order("كم قيمة غرامة التأخير اليومية؟", pool)
    check(
        ranked[0] == "relevant",
        "المقطع الذي يجيب السؤال يظل أولاً",
        str(ranked),
    )


def adjustments_are_explainable() -> None:
    print("\n-- 7. the demotion is recorded, and nothing is promoted --")

    analysis = ANALYZER.analyze("ما بنود مأمورية الخبير؟")
    ranked = RERANKER.rerank(analysis, [ORIGINAL, EARLIER, EFFECTIVE], limit=3)
    by_id = {c.chunk_id: c for c in ranked}

    replaced = by_id["earlier"]
    check(replaced.standing == "superseded", "المتجاوَزة موسومة", replaced.standing)
    check(replaced.standing_adjustment < 0, "والخصم مسجَّل كرقم", str(replaced.standing_adjustment))
    check(bool(replaced.rank_notes), "وملاحظة تشرحه", str(replaced.rank_notes))

    # The version in force gains nothing. It wins by the other one losing, and that is
    # what stops an in-force passage about a different subject jumping the queue.
    in_force = by_id["effective"]
    check(in_force.standing == "current", "السارية موسومة كسارية", in_force.standing)
    check(
        in_force.standing_adjustment == 0.0,
        "ولا تُمنح أي زيادة",
        str(in_force.standing_adjustment),
    )


def main() -> None:
    print("supersession — which version is in force")
    standing_is_read()
    a_correction_is_not_a_casualty()
    the_version_in_force_wins()
    retired_passages_sink()
    ordinary_questions_are_untouched()
    relevance_still_outranks_standing()
    adjustments_are_explainable()

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("the corrected version wins, and passages that claim nothing are left alone")


if __name__ == "__main__":
    main()
