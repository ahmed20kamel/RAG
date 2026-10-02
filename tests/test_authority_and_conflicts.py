"""Source priority and conflict handling, tested as pure logic.

The property under test is not "does it pick the right source" but "does it refuse to
pick when nothing entitles it to". Every case is built from metadata shapes — a status, a
rank, a date, a scope — never from a particular document or question, so a rule that only
worked for one file would pass none of these.

Deterministic and offline: no database, no model, no network.

Run: python tests/test_authority_and_conflicts.py
"""

from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.authority import Basis, SourceAuthority, resolve  # noqa: E402
from app.core.knowledge import KnowledgeScope, KnowledgeType  # noqa: E402
from app.services.conflict_detector import (  # noqa: E402
    ConflictDetector,
    knowledge_authorities,
)
from app.services.knowledge_service import ActiveKnowledge  # noqa: E402

FAILURES: list[str] = []
DETECTOR = ConflictDetector()


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def doc(citation: int, excerpt: str, filename: str = "f.md", document_id: str = "d1", version: str = ""):
    """A stand-in for a retrieved passage: the detector reads only these fields."""

    class _Source:
        def __init__(self) -> None:
            self.citation = citation
            self.excerpt = excerpt
            self.filename = filename
            self.document_id = document_id
            self.version = version

    return _Source()


def taught(content: str, item_id: str = "k1", scope=KnowledgeScope.GLOBAL, source_text: str = "") -> ActiveKnowledge:
    return ActiveKnowledge(
        item_id=item_id,
        version_id=f"{item_id}-v1",
        type=KnowledgeType.FACT,
        scope=scope,
        content=content,
        source_text=source_text,
        source_document_id=None,
        confidence=0.8,
        version_no=1,
        tags=[],
    )


# -- 1. status decides, because a superseded document is not evidence -----
def status_decides() -> None:
    print("\n-- 1. a document that is not in force cannot outrank one that is --")
    active = SourceAuthority(ref="doc:1", kind="document", status="active", label="النافذ")
    superseded = SourceAuthority(ref="doc:2", kind="document", status="superseded", label="الملغى")

    decision = resolve(active, superseded)
    check(decision.resolved, "a disagreement with a superseded document is resolvable")
    check(decision.winner.ref == "doc:1", "the document in force wins")
    check(decision.basis is Basis.STATUS, f"and the reason is recorded as status ({decision.basis})")
    check("الملغى" in decision.explanation, "the explanation names the source that lost")

    both_dead = resolve(superseded, SourceAuthority(ref="doc:3", kind="document", status="draft"))
    check(not both_dead.resolved, "two sources that are both out of force settle nothing")


# -- 2. an explicit rank decides ------------------------------------------
def explicit_rank_decides() -> None:
    print("\n-- 2. an operator-assigned rank decides --")
    high = SourceAuthority(ref="doc:1", kind="document", authority=10, label="العقد")
    low = SourceAuthority(ref="doc:2", kind="document", authority=3, label="مذكرة")

    decision = resolve(high, low)
    check(decision.resolved and decision.winner.ref == "doc:1", "the higher rank wins")
    check(decision.basis is Basis.AUTHORITY_RANK, "the reason is the declared rank")

    equal = resolve(
        SourceAuthority(ref="doc:1", kind="document", authority=5),
        SourceAuthority(ref="doc:2", kind="document", authority=5),
    )
    check(not equal.resolved, "equal ranks settle nothing")

    undeclared = resolve(
        SourceAuthority(ref="doc:1", kind="document"),
        SourceAuthority(ref="doc:2", kind="document"),
    )
    check(not undeclared.resolved, "two documents with no declared metadata settle nothing")


# -- 3. dates and versions, only where they are comparable ----------------
def dates_and_versions_decide() -> None:
    print("\n-- 3. a later effective date or higher version decides --")
    newer = SourceAuthority(ref="doc:1", kind="document", effective_date=date(2026, 3, 1), label="أحدث")
    older = SourceAuthority(ref="doc:2", kind="document", effective_date=date(2025, 8, 1), label="أقدم")
    decision = resolve(newer, older)
    check(decision.resolved and decision.winner.ref == "doc:1", "the later effective date wins")
    check(decision.basis is Basis.EFFECTIVE_DATE, "the reason is the date")

    one_sided = resolve(
        SourceAuthority(ref="doc:1", kind="document", effective_date=date(2026, 3, 1)),
        SourceAuthority(ref="doc:2", kind="document"),
    )
    check(not one_sided.resolved, "a date on only one side decides nothing")

    versions = resolve(
        SourceAuthority(ref="doc:1", kind="document", version="2.1"),
        SourceAuthority(ref="doc:2", kind="document", version="1.9"),
    )
    check(versions.resolved and versions.basis is Basis.VERSION, "a higher numeric version wins")

    unparseable = resolve(
        SourceAuthority(ref="doc:1", kind="document", version="final"),
        SourceAuthority(ref="doc:2", kind="document", version="draft-2"),
    )
    check(
        not unparseable.resolved,
        "versions that are not comparable numbers decide nothing rather than being guessed at",
    )


# -- 4. a document outranks a taught claim, by comparison not instruction --
def document_outranks_knowledge() -> None:
    print("\n-- 4. document evidence outranks approved knowledge --")
    document = SourceAuthority(ref="doc:1", kind="document", status="active", label="العقد")
    knowledge = SourceAuthority(ref="knowledge:k1", kind="knowledge", status="active", label="قرار الإدارة")

    decision = resolve(knowledge, document)
    check(decision.resolved, "the pair is resolvable")
    check(decision.winner.kind == "document", "the document wins regardless of argument order")
    check(
        decision.basis is Basis.DOCUMENT_OVER_KNOWLEDGE,
        "the reason names the priority rule, so it is auditable",
    )

    reversed_order = resolve(document, knowledge)
    check(reversed_order.winner.ref == "doc:1", "the order of the arguments does not change it")


# -- 5. a wider knowledge scope was approved by a wider authority ---------
def knowledge_scope_decides() -> None:
    print("\n-- 5. between taught claims, the wider scope wins --")
    company = SourceAuthority(ref="knowledge:a", kind="knowledge", scope_rank=3, label="عام")
    personal = SourceAuthority(ref="knowledge:b", kind="knowledge", scope_rank=0, label="شخصي")

    decision = resolve(company, personal)
    check(decision.resolved and decision.winner.ref == "knowledge:a", "the company-wide item wins")
    check(decision.basis is Basis.KNOWLEDGE_SCOPE, "the reason is the scope")

    same = resolve(
        SourceAuthority(ref="knowledge:a", kind="knowledge", scope_rank=3),
        SourceAuthority(ref="knowledge:b", kind="knowledge", scope_rank=3),
    )
    check(not same.resolved, "two company-wide claims settle nothing between them")


# -- 6. THE CENTRAL CASE: two documents, different numbers, no authority --
def ambiguous_documents_are_never_silently_resolved() -> None:
    print("\n-- 6. two documents disagree and nothing entitles either to win --")
    sources = [
        doc(1, "قيمة غرامة التأخير القصوى للمشروع هي 268,000 درهم", filename="أ.md", document_id="d1"),
        doc(2, "قيمة غرامة التأخير القصوى للمشروع هي 310,000 درهم", filename="ب.md", document_id="d2"),
    ]
    # Neither document declares a status, a rank, a date or a version.
    conflicts = DETECTOR.detect(sources, [], document_authority={})

    check(len(conflicts) == 1, f"the disagreement is detected ({len(conflicts)})")
    conflict = conflicts[0]
    check(not conflict.resolved, "and it is NOT resolved")
    check(conflict.decision.basis is Basis.UNRESOLVED, "the basis is recorded as unresolved")

    described = conflict.describe()
    check("268,000" in described or "268000" in described, "the first value is preserved")
    check("310,000" in described or "310000" in described, "the second value is preserved")
    check("[1]" in described and "[2]" in described, "both citations are preserved")
    check("فاعرضهما معًا" in described, "the instruction is to show both, not to choose")


def ambiguous_documents_resolve_once_metadata_exists() -> None:
    print("\n-- 6b. the same pair resolves once an operator declares authority --")
    sources = [
        doc(1, "قيمة غرامة التأخير القصوى للمشروع هي 268,000 درهم", document_id="d1"),
        doc(2, "قيمة غرامة التأخير القصوى للمشروع هي 310,000 درهم", document_id="d2"),
    ]
    authority = {
        "doc:1": SourceAuthority(ref="doc:1", kind="document", status="active", label="النافذ"),
        "doc:2": SourceAuthority(ref="doc:2", kind="document", status="superseded", label="الملغى"),
    }
    conflicts = DETECTOR.detect(sources, [], document_authority=authority)
    check(len(conflicts) == 1, "the same disagreement is still detected")
    check(conflicts[0].resolved, "now it resolves, because the metadata says which is in force")
    check(conflicts[0].winner_ref == "doc:1", "the document in force wins")
    check(
        "رُجّح" in conflicts[0].describe(),
        "and the report says a decision was made and why",
    )


# -- 7. knowledge against knowledge ---------------------------------------
def knowledge_conflicts_with_knowledge() -> None:
    print("\n-- 7. two taught claims that disagree --")
    items = [
        taught("مدة الإشعار المسبق للمقاول 30 يومًا", item_id="k1", scope=KnowledgeScope.GLOBAL),
        taught("مدة الإشعار المسبق للمقاول 15 يومًا", item_id="k2", scope=KnowledgeScope.TEAM),
    ]
    conflicts = DETECTOR.detect([], items, knowledge_authority=knowledge_authorities(items))
    check(len(conflicts) == 1, "the disagreement between two taught claims is detected")
    check(conflicts[0].resolved, "scope resolves it")
    check(conflicts[0].winner_ref == "knowledge:k1", "the company-wide claim wins over the team one")

    same_scope = [
        taught("مدة الإشعار المسبق للمقاول 30 يومًا", item_id="k1"),
        taught("مدة الإشعار المسبق للمقاول 15 يومًا", item_id="k2"),
    ]
    tied = DETECTOR.detect([], same_scope, knowledge_authority=knowledge_authorities(same_scope))
    check(len(tied) == 1, "two claims at the same scope still conflict")
    check(not tied[0].resolved, "and nothing settles it, so both are shown")


# -- 8. knowledge against a document --------------------------------------
def knowledge_conflicts_with_document() -> None:
    print("\n-- 8. a taught claim that contradicts a document --")
    items = [taught("قيمة الدفعة المقدمة للمشروع 150,000 درهم", item_id="k1")]
    sources = [doc(1, "قيمة الدفعة المقدمة للمشروع 120,000 درهم", document_id="d1")]

    conflicts = DETECTOR.detect(
        sources, items, knowledge_authority=knowledge_authorities(items),
        document_authority={
            "doc:1": SourceAuthority(ref="doc:1", kind="document", status="active", label="العقد")
        },
    )
    check(len(conflicts) == 1, "the disagreement is detected")
    check(conflicts[0].resolved, "the priority rule settles it")
    check(conflicts[0].winner_ref == "doc:1", "the document wins over the taught claim")
    described = conflicts[0].describe()
    check("150,000" in described or "150000" in described, "the taught value is still reported")
    check("120,000" in described or "120000" in described, "alongside the document value")


# -- 9. coincidences are not conflicts ------------------------------------
def coincidences_are_not_conflicts() -> None:
    print("\n-- 9. different numbers about different things are not a disagreement --")
    sources = [
        doc(1, "عدد العمال في الموقع 45 عاملًا", document_id="d1"),
        doc(2, "مدة التنفيذ 540 يومًا", document_id="d2"),
    ]
    check(DETECTOR.detect(sources, []) == [], "unrelated passages raise nothing")

    agreeing = [
        doc(1, "قيمة العقد 2,680,000 درهم معتمدة", document_id="d1"),
        doc(2, "قيمة العقد 2,680,000 درهم كما في الملحق", document_id="d2"),
    ]
    check(DETECTOR.detect(agreeing, []) == [], "passages that agree raise nothing")

    one_word = [
        doc(1, "الرقم 12", document_id="d1"),
        doc(2, "الرقم 99", document_id="d2"),
    ]
    check(
        DETECTOR.detect(one_word, []) == [],
        "a single shared word is too thin to call a disagreement",
    )


# -- 9b. values spelled out in words are compared like digits ------------
def numbers_written_as_words_are_compared() -> None:
    """This was recorded as a known limitation, and the test asserted the miss.

    The concern that kept it unfixed was that a wrong parse of Arabic number words
    would invent a conflict that does not exist. So the capability is asserted together
    with its guard: the same value written once in words and once in digits must not
    be reported, and neither must a fraction that looks like a number ("عُشر").
    """
    print("\n-- 9b. values spelled out in words are compared like digits --")
    disagree = [
        doc(1, "مدة الإشعار المسبق للمقاول ثلاثون يومًا", document_id="d1"),
        doc(2, "مدة الإشعار المسبق للمقاول خمسة عشر يومًا", document_id="d2"),
    ]
    check(
        len(DETECTOR.detect(disagree, [])) == 1,
        "a disagreement written only in words is detected",
    )

    mixed = [
        doc(1, "مدة الإشعار المسبق للمقاول ثلاثون يومًا", document_id="d1"),
        doc(2, "مدة الإشعار المسبق للمقاول 45 يومًا", document_id="d2"),
    ]
    check(len(DETECTOR.detect(mixed, [])) == 1, "and one written in words against digits")

    same = [
        doc(1, "مدة الإشعار المسبق للمقاول ثلاثون يومًا", document_id="d1"),
        doc(2, "مدة الإشعار المسبق للمقاول 30 يومًا", document_id="d2"),
    ]
    check(DETECTOR.detect(same, []) == [], "the same value in words and digits is not a conflict")

    fraction = [
        doc(1, "تُخصم نسبة عشر قيمة الدفعة عند التأخير", document_id="d1"),
        doc(2, "تُخصم نسبة 5 من قيمة الدفعة عند التأخير", document_id="d2"),
    ]
    check(
        all("10" not in c.describe() for c in DETECTOR.detect(fraction, [])),
        "«عُشر» is a fraction, never read as ten",
    )


# -- 10. rules and preferences never conflict -----------------------------
def directives_never_conflict() -> None:
    print("\n-- 10. a rule or preference asserts nothing, so it cannot disagree --")
    rule = ActiveKnowledge(
        item_id="r1", version_id="r1-v1", type=KnowledgeType.RULE, scope=KnowledgeScope.GLOBAL,
        content="اعرض دائمًا القيمتين عند التعارض، ولو كان الفرق 100 درهم",
        source_text="", source_document_id=None, confidence=1.0, version_no=1, tags=[],
    )
    sources = [doc(1, "قيمة الفرق المسموح بها 250 درهم عند التعارض", document_id="d1")]
    check(
        DETECTOR.detect(sources, [rule]) == [],
        "a rule is never treated as a competing factual claim",
    )


if __name__ == "__main__":
    status_decides()
    explicit_rank_decides()
    dates_and_versions_decide()
    document_outranks_knowledge()
    knowledge_scope_decides()
    ambiguous_documents_are_never_silently_resolved()
    ambiguous_documents_resolve_once_metadata_exists()
    knowledge_conflicts_with_knowledge()
    knowledge_conflicts_with_document()
    coincidences_are_not_conflicts()
    numbers_written_as_words_are_compared()
    directives_never_conflict()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All authority and conflict checks passed.")
