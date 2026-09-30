"""A document's bookkeeping against what the document is about.

The measured failure: asked for the current position of a matter, the system spent six
of its fourteen retrieval slots on the file's own changelog, its list of generated files
and an integrity card — because the question contained the words "file", "update" and
"latest", and those sections are titled with exactly those words. Nearly half the
evidence budget went to bookkeeping and the decisive section never entered the answer.

The property under test is a re-weighting, not a ban. Bookkeeping sections are real
content and answer real questions; what they must not do is win a question about the
subject on the strength of a shared word. So every case below is paired: the same
section, asked about in two different ways, must rank differently.

Deterministic and offline: no database, no model, no network.

Run: python tests/test_metadata_vs_substantive.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.retrieval import Candidate  # noqa: E402
from app.services.evidence_class import (  # noqa: E402
    classify_section,
    query_wants_metadata,
)
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.reranking import FeatureReranker  # noqa: E402

FAILURES: list[str] = []
ANALYZER = QueryAnalyzer()
RERANKER = FeatureReranker()


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def candidate(chunk_id: str, section: str, content: str, *, vector: float = 0.62) -> Candidate:
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
# 1. Classifying a section
# ---------------------------------------------------------------------------


def sections_are_classified() -> None:
    print("\n-- 1. which sections are the document talking about itself --")

    bookkeeping = [
        "الملف → 13. سجل إصدارات هذا الملف",
        "الملف → سجل التعديلات",
        "الملف → 11. الملفات والتقارير المنتجة",
        "الملف → جدول المحتويات",
        "الملف → بطاقة سلامة سياقية",
        "الملف → 12. سجل المحادثات (مرجعي فقط)",
        "File → Version history",
        "File → Change log",
        "File → Generated files",
        "File → Table of contents",
        "File → Document metadata",
    ]
    for section in bookkeeping:
        check(classify_section(section).is_metadata, f"bookkeeping: {section}")

    subject = [
        "الملف → 3. الحكم القطعي (15/08/2026)",
        "الملف → 4.1 سير الجلسات ومذكرات الأطراف",
        "الملف → 7. البنود المفتوحة الرئيسية",
        "الملف → 2. بيانات العقد والرخصة المعتمدة",
        "الملف → غرامة التأخير — Sub-Clause 8.7",
        "Project → Payment schedule",
        "Project → Scope of works",
    ]
    for section in subject:
        check(not classify_section(section).is_metadata, f"subject: {section}")


def queries_are_classified() -> None:
    print("\n-- 2. which questions are about the document as an object --")

    about_the_file = [
        "ما آخر إصدار من هذا الملف؟",
        "ما سجل التعديلات على المستند؟",
        "كم ملف أُنتج في هذا المشروع؟",
        "ما قائمة الملفات المنتجة؟",
        "what is the changelog?",
        "show me the version history",
        "how many files were produced?",
        "what is the table of contents?",
    ]
    for question in about_the_file:
        check(query_wants_metadata(question).is_metadata, f"about the file: {question}")

    # The decisive set. Every one of these contains a word that appears in a
    # bookkeeping heading, and not one of them is a question about bookkeeping.
    about_the_subject = [
        "ما آخر مرحلة وصلت إليها القضية بحسب أحدث تحديث موثق في الملف؟",
        "ما آخر تحديث في ملف المشروع؟",
        "ما الوضع الحالي للمشروع؟",
        "ما إجراءات المطالبات وفق FIDIC؟",
        "what is the latest update on the claim file?",
        "what is the current status of the document review?",
    ]
    for question in about_the_subject:
        check(
            not query_wants_metadata(question).is_metadata,
            f"about the subject: {question}",
            f"fired on {query_wants_metadata(question).cue!r}",
        )


# ---------------------------------------------------------------------------
# 3. Ranking — the behaviour that actually failed
# ---------------------------------------------------------------------------


SUBSTANTIVE = candidate(
    "ruling",
    "الملف → 3. الحكم القطعي (15/08/2026)",
    "حكمت المحكمة بانتهاء الدعوى بشأن الملف وفقاً لأحدث تحديث في تقرير الخبرة.",
)
CHANGELOG = candidate(
    "changelog",
    "الملف → 13. سجل إصدارات هذا الملف",
    "سجل تحديث الملف: الإصدار الأحدث يضيف أقساماً ويصحح أرقام المراسلات.",
)
FILE_LIST = candidate(
    "files",
    "الملف → 11. الملفات والتقارير المنتجة",
    "قائمة الملفات المنتجة مع حالة كل ملف وآخر تحديث لكل تقرير.",
)


def subject_questions_demote_bookkeeping() -> None:
    print("\n-- 3. asked about the subject, bookkeeping loses its word-match advantage --")

    pool = [CHANGELOG, FILE_LIST, SUBSTANTIVE]
    ranked = order("ما آخر مرحلة وصلت إليها القضية بحسب أحدث تحديث موثق في الملف؟", pool)
    check(ranked[0] == "ruling", "the substantive section ranks first", str(ranked))
    check(
        ranked.index("changelog") > 0 and ranked.index("files") > 0,
        "and both bookkeeping sections rank below it",
        str(ranked),
    )


def metadata_questions_keep_bookkeeping() -> None:
    print("\n-- 4. asked about the file, bookkeeping is what the reader wants --")

    pool = [SUBSTANTIVE, CHANGELOG, FILE_LIST]
    ranked = order("ما سجل إصدارات هذا الملف؟", pool)
    check(ranked[0] == "changelog", "the changelog ranks first", str(ranked))

    ranked = order("ما قائمة الملفات المنتجة؟", pool)
    check(ranked[0] == "files", "the file inventory ranks first", str(ranked))

    ranked = order("what is the version history of this document?", pool)
    check(ranked[0] == "changelog", "and the same in English", str(ranked))


def bookkeeping_is_never_excluded() -> None:
    print("\n-- 5. a penalty, not a filter --")

    analysis = ANALYZER.analyze("ما آخر مرحلة في القضية؟")
    ranked = RERANKER.rerank(analysis, [CHANGELOG, FILE_LIST, SUBSTANTIVE], limit=10)
    check(len(ranked) == 3, "every candidate is still returned", str(len(ranked)))
    demoted = [c for c in ranked if c.is_metadata]
    check(len(demoted) == 2, "both bookkeeping sections are still there")
    check(
        all(c.metadata_adjustment < 0 for c in demoted),
        "each carries the adjustment that was applied",
        str([(c.chunk_id, c.metadata_adjustment) for c in demoted]),
    )
    check(
        all(c.rank_notes for c in demoted),
        "and a note saying why",
        str([c.rank_notes for c in demoted]),
    )


def substantive_sections_are_untouched() -> None:
    print("\n-- 6. subject sections carry no adjustment at all --")

    analysis = ANALYZER.analyze("ما إجراءات المطالبات وفق FIDIC؟")
    ranked = RERANKER.rerank(analysis, [SUBSTANTIVE], limit=1)
    check(not ranked[0].is_metadata, "a subject section is not classed as bookkeeping")
    check(ranked[0].metadata_adjustment == 0.0, "and receives no adjustment")


def a_bookkeeping_answer_can_still_win() -> None:
    print("\n-- 7. bookkeeping that genuinely answers the question still wins --")

    # The question is about the subject, so the changelog is penalised — and it still
    # comes first, because it is the only passage that addresses what was asked. A
    # filter would have made this outcome impossible.
    only_relevant = candidate(
        "changelog",
        "الملف → سجل التعديلات",
        "أُضيف بند التعويض عن التأخير إلى الإصدار الثالث بعد اعتماد المهندس.",
        vector=0.93,
    )
    unrelated = candidate(
        "other",
        "الملف → السلامة في الموقع",
        "تُركّب حواجز الحماية حول الحفر قبل بدء أعمال التدعيم والصب.",
        vector=0.18,
    )
    ranked = order("متى أُضيف بند التعويض عن التأخير؟", [unrelated, only_relevant])
    check(
        ranked[0] == "changelog",
        "the penalised section still wins when it is the answer",
        str(ranked),
    )


def main() -> None:
    print("metadata versus substantive evidence")
    sections_are_classified()
    queries_are_classified()
    subject_questions_demote_bookkeeping()
    metadata_questions_keep_bookkeeping()
    bookkeeping_is_never_excluded()
    substantive_sections_are_untouched()
    a_bookkeeping_answer_can_still_win()

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("bookkeeping answers questions about the file, and stops crowding out the rest")


if __name__ == "__main__":
    main()
