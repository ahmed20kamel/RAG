"""The knowledge layer, tested against its own guarantees.

Every case here is about a property, not about a particular question or document: that
nothing unapproved reaches an answer, that a personal note stays personal, that history
is append-only, and that a disagreement with a document is shown rather than settled.

Deterministic and offline — a temporary SQLite file, no model, no network.

Run: python tests/test_knowledge_layer.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_TMP = Path(tempfile.mkdtemp(prefix="rag-knowledge-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'knowledge.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")

from app.core.knowledge import (  # noqa: E402
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    can_transition,
)
from app.core.permissions import Role  # noqa: E402
from app.exceptions import AuthorizationError, ValidationError  # noqa: E402
from app.models.auth import Team, User  # noqa: E402
from app.models.database import Base, SessionLocal, engine  # noqa: E402
from app.models.knowledge_items import KnowledgeVersion  # noqa: E402
from app.services.knowledge_arm import KnowledgeArm  # noqa: E402
from app.services.knowledge_service import (  # noqa: E402
    KnowledgeService,
    ProposedKnowledge,
)

FAILURES: list[str] = []
SERVICE = KnowledgeService()
ARM = KnowledgeArm()


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def setup_database() -> None:
    from app.models import auth, document, knowledge, knowledge_items  # noqa: F401

    Base.metadata.create_all(bind=engine)


def make_user(db, role: str, team_id: str | None = None) -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{role}-{uuid.uuid4().hex[:6]}@local",
        display_name=role,
        password_hash="x",
        role=role,
        team_id=team_id,
    )
    db.add(user)
    db.flush()
    return user


def make_team(db, name: str) -> Team:
    team = Team(id=str(uuid.uuid4()), name=f"{name}-{uuid.uuid4().hex[:6]}")
    db.add(team)
    db.flush()
    return team


def source(text: str, citation: int = 1):
    """A stand-in for a retrieved passage: the arm only reads excerpt and citation."""

    class _Source:
        def __init__(self) -> None:
            self.excerpt = text
            self.citation = citation

    return _Source()


# -- 1. nothing unapproved is ever retrievable ----------------------------
def unapproved_never_retrievable() -> None:
    print("\n-- 1. only ACTIVE knowledge is retrievable --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        approver = make_user(db, Role.ADMIN)

        item = SERVICE.propose(
            db, author,
            ProposedKnowledge(
                type=KnowledgeType.FACT,
                content="مدة الضمان للمشروع اثنا عشر شهرًا",
                scope=KnowledgeScope.GLOBAL,
            ),
        )
        db.flush()
        check(item.status == KnowledgeStatus.PENDING, "a proposal starts PENDING, not active")
        check(
            not any(k.item_id == item.id for k in SERVICE.active_for(db, approver)),
            "PENDING knowledge is not retrievable",
        )

        SERVICE.transition(db, approver, item, KnowledgeStatus.IN_REVIEW)
        check(
            not any(k.item_id == item.id for k in SERVICE.active_for(db, approver)),
            "IN_REVIEW knowledge is not retrievable",
        )

        SERVICE.transition(db, approver, item, KnowledgeStatus.APPROVED)
        check(
            not any(k.item_id == item.id for k in SERVICE.active_for(db, approver)),
            "APPROVED but not yet activated is still not retrievable",
        )

        SERVICE.transition(db, approver, item, KnowledgeStatus.ACTIVE)
        check(
            any(k.item_id == item.id for k in SERVICE.active_for(db, approver)),
            "ACTIVE knowledge is retrievable",
        )
        db.rollback()


# -- 2. rejected and archived stay out ------------------------------------
def rejected_and_archived_excluded() -> None:
    print("\n-- 2. rejected and archived knowledge cannot come back by accident --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        rejected = SERVICE.propose(
            db, author,
            ProposedKnowledge(type=KnowledgeType.FACT, content="قيمة مرفوضة", scope=KnowledgeScope.GLOBAL),
        )
        SERVICE.transition(db, admin, rejected, KnowledgeStatus.REJECTED, reason="غير مدعومة بمصدر")
        check(
            not any(k.item_id == rejected.id for k in SERVICE.active_for(db, admin)),
            "REJECTED knowledge is excluded",
        )

        archived = SERVICE.propose(
            db, author,
            ProposedKnowledge(type=KnowledgeType.FACT, content="قيمة قديمة", scope=KnowledgeScope.GLOBAL),
        )
        SERVICE.transition(db, admin, archived, KnowledgeStatus.APPROVED)
        SERVICE.transition(db, admin, archived, KnowledgeStatus.ACTIVE)
        check(
            any(k.item_id == archived.id for k in SERVICE.active_for(db, admin)),
            "the item is retrievable while active",
        )
        SERVICE.transition(db, admin, archived, KnowledgeStatus.ARCHIVED, reason="حلّت محلها نسخة أحدث")
        check(
            not any(k.item_id == archived.id for k in SERVICE.active_for(db, admin)),
            "archiving stops it reaching answers immediately",
        )

        # Restoring puts it back in the queue, not back into use.
        SERVICE.transition(db, admin, archived, KnowledgeStatus.PENDING)
        check(archived.status == KnowledgeStatus.PENDING, "restore returns it to review")
        check(
            not any(k.item_id == archived.id for k in SERVICE.active_for(db, admin)),
            "a restored item is still not retrievable until re-approved",
        )
        db.rollback()


# -- 3. scope isolation ---------------------------------------------------
def scope_isolation() -> None:
    print("\n-- 3. a personal note stays personal; a team note stays in the team --")
    with SessionLocal() as db:
        team_a, team_b = make_team(db, "a"), make_team(db, "b")
        alice = make_user(db, Role.CONTRIBUTOR, team_id=team_a.id)
        bob = make_user(db, Role.CONTRIBUTOR, team_id=team_b.id)
        admin = make_user(db, Role.ADMIN)

        personal = SERVICE.propose(
            db, alice,
            ProposedKnowledge(
                type=KnowledgeType.PREFERENCE, content="أجب باختصار", scope=KnowledgeScope.USER
            ),
        )
        check(personal.status == KnowledgeStatus.ACTIVE, "a personal preference activates for its owner")
        check(
            any(k.item_id == personal.id for k in SERVICE.active_for(db, alice)),
            "the owner sees their own preference",
        )
        check(
            not any(k.item_id == personal.id for k in SERVICE.active_for(db, bob)),
            "nobody else does",
        )

        team_item = SERVICE.propose(
            db, alice,
            ProposedKnowledge(
                type=KnowledgeType.FACT, content="فريق أ يعتمد نموذج التسليم الثاني",
                scope=KnowledgeScope.TEAM,
            ),
        )
        SERVICE.transition(db, admin, team_item, KnowledgeStatus.APPROVED)
        SERVICE.transition(db, admin, team_item, KnowledgeStatus.ACTIVE)
        check(
            any(k.item_id == team_item.id for k in SERVICE.active_for(db, alice)),
            "a team member sees team knowledge",
        )
        check(
            not any(k.item_id == team_item.id for k in SERVICE.active_for(db, bob)),
            "another team does not",
        )

        # A personal *fact* is a claim, so it still needs review — the fast path is
        # deliberately limited to preferences.
        personal_fact = SERVICE.propose(
            db, alice,
            ProposedKnowledge(type=KnowledgeType.FACT, content="رقم المشروع 991", scope=KnowledgeScope.USER),
        )
        check(
            personal_fact.status == KnowledgeStatus.PENDING,
            "a personal FACT still requires approval",
        )
        db.rollback()


# -- 4. approval authority ------------------------------------------------
def approval_authority() -> None:
    print("\n-- 4. approval authority follows the reach of the item --")
    with SessionLocal() as db:
        team = make_team(db, "ops")
        manager = make_user(db, Role.KNOWLEDGE_MANAGER, team_id=team.id)
        other_manager = make_user(db, Role.KNOWLEDGE_MANAGER, team_id=make_team(db, "other").id)
        contributor = make_user(db, Role.CONTRIBUTOR, team_id=team.id)
        admin = make_user(db, Role.ADMIN)

        team_item = SERVICE.propose(
            db, contributor,
            ProposedKnowledge(type=KnowledgeType.FACT, content="إجراء الفريق", scope=KnowledgeScope.TEAM),
        )
        check(SERVICE.may_approve(manager, team_item), "a manager may approve their own team's knowledge")
        check(
            not SERVICE.may_approve(other_manager, team_item),
            "a manager of another team may not",
        )
        check(not SERVICE.may_approve(contributor, team_item), "a contributor may not approve")

        global_item = SERVICE.propose(
            db, contributor,
            ProposedKnowledge(type=KnowledgeType.RULE, content="اعرض التعارض دائمًا", scope=KnowledgeScope.GLOBAL),
        )
        check(
            not SERVICE.may_approve(manager, global_item),
            "company-wide knowledge is not a manager's call",
        )
        check(SERVICE.may_approve(admin, global_item), "an administrator may approve it")

        raised = False
        try:
            SERVICE.transition(db, manager, global_item, KnowledgeStatus.APPROVED)
        except AuthorizationError:
            raised = True
        check(raised, "the attempt is refused, not merely hidden in the interface")
        db.rollback()


# -- 5. versioning is append-only -----------------------------------------
def versioning_is_append_only() -> None:
    print("\n-- 5. history is append-only and approval does not survive an edit --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        item = SERVICE.propose(
            db, author,
            ProposedKnowledge(
                type=KnowledgeType.FACT, content="النسخة الأولى", scope=KnowledgeScope.GLOBAL,
                source_text="محضر الاجتماع",
            ),
        )
        first_version_id = item.active_version_id
        SERVICE.transition(db, admin, item, KnowledgeStatus.APPROVED)
        SERVICE.transition(db, admin, item, KnowledgeStatus.ACTIVE)

        SERVICE.edit(db, author, item, content="النسخة الثانية", change_reason="تصحيح رقم")
        db.flush()

        versions = SERVICE.versions(db, item.id)
        check(len(versions) == 2, f"an edit appends a version ({len(versions)})")
        check(
            db.get(KnowledgeVersion, first_version_id).content == "النسخة الأولى",
            "the approved wording is still readable afterwards",
        )
        check(item.active_version_id != first_version_id, "the item points at the new version")
        check(
            item.status == KnowledgeStatus.PENDING,
            "editing an active item withdraws its approval",
        )
        check(item.approved_by is None, "and clears who approved it")
        check(
            not any(k.item_id == item.id for k in SERVICE.active_for(db, admin)),
            "so the edited wording does not reach answers unreviewed",
        )
        check(
            [v.version_no for v in versions] == [2, 1],
            "versions are numbered in sequence",
        )
        db.rollback()


# -- 6. the state machine refuses invented paths --------------------------
def transitions_are_constrained() -> None:
    print("\n-- 6. only declared transitions are possible --")
    check(can_transition(KnowledgeStatus.PENDING, KnowledgeStatus.APPROVED), "pending -> approved")
    check(can_transition(KnowledgeStatus.APPROVED, KnowledgeStatus.ACTIVE), "approved -> active")
    check(
        not can_transition(KnowledgeStatus.PENDING, KnowledgeStatus.ACTIVE),
        "pending -> active is refused: activation requires approval first",
    )
    check(
        not can_transition(KnowledgeStatus.REJECTED, KnowledgeStatus.ACTIVE),
        "rejected -> active is refused",
    )
    check(
        not can_transition(KnowledgeStatus.ARCHIVED, KnowledgeStatus.ACTIVE),
        "archived -> active is refused; it must be reviewed again",
    )

    with SessionLocal() as db:
        admin = make_user(db, Role.ADMIN)
        item = SERVICE.propose(
            db, admin,
            ProposedKnowledge(type=KnowledgeType.FACT, content="قيمة", scope=KnowledgeScope.GLOBAL),
        )
        raised = False
        try:
            SERVICE.transition(db, admin, item, KnowledgeStatus.ACTIVE)
        except ValidationError:
            raised = True
        check(raised, "the service refuses the jump, not just the UI")

        rejected_without_reason = False
        try:
            SERVICE.transition(db, admin, item, KnowledgeStatus.REJECTED)
        except ValidationError:
            rejected_without_reason = True
        check(rejected_without_reason, "a rejection must say why")
        db.rollback()


# -- 7. source attribution survives -----------------------------------------
def source_attribution() -> None:
    print("\n-- 7. every item carries where it came from --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)
        item = SERVICE.propose(
            db, author,
            ProposedKnowledge(
                type=KnowledgeType.FACT,
                content="مدة الإشعار ثلاثون يومًا",
                scope=KnowledgeScope.GLOBAL,
                source_text="البند 14 من العقد",
                source_document_id="doc-123",
                confidence=0.9,
            ),
        )
        SERVICE.transition(db, admin, item, KnowledgeStatus.APPROVED)
        SERVICE.transition(db, admin, item, KnowledgeStatus.ACTIVE)

        retrieved = next(k for k in SERVICE.active_for(db, admin) if k.item_id == item.id)
        check(retrieved.source_text == "البند 14 من العقد", "the stated source reaches retrieval")
        check(retrieved.source_document_id == "doc-123", "the linked document reaches retrieval")
        check(retrieved.confidence == 0.9, "the author's confidence is preserved")
        check(item.created_by == author.id, "the author is recorded")
        check(item.approved_by == admin.id, "the approver is recorded")

        reviews = SERVICE.reviews(db, item.id)
        decisions = [r.decision for r in reviews]
        check(
            decisions == ["submitted", "approved", "activated"],
            f"the audit trail records every step in order ({decisions})",
        )
        db.rollback()


# -- 8. the arm is inert when there is nothing approved -------------------
def arm_is_neutral_when_empty() -> None:
    print("\n-- 8. with nothing approved the arm changes nothing --")
    contribution = ARM.select("ما مدة الضمان؟", [])
    check(contribution.is_empty, "an empty knowledge base yields an empty contribution")
    check(ARM.render_facts(contribution) == "", "no fact block is produced")
    check(ARM.render_directives(contribution) == "", "no directive block is produced")
    check(contribution.conflicts == [], "and no conflicts are reported")


# -- 9. rules and preferences are never evidence ---------------------------
def directives_are_not_evidence() -> None:
    print("\n-- 9. a rule or preference never becomes a stated fact --")
    with SessionLocal() as db:
        admin = make_user(db, Role.ADMIN)
        for kind, content in (
            (KnowledgeType.RULE, "عند تعارض مستندين اعرض القيمتين ومصدر كل منهما"),
            (KnowledgeType.PREFERENCE, "أجب بالعربية المختصرة"),
        ):
            item = SERVICE.propose(
                db, admin,
                ProposedKnowledge(type=kind, content=content, scope=KnowledgeScope.GLOBAL),
            )
            if item.status != KnowledgeStatus.ACTIVE:
                SERVICE.transition(db, admin, item, KnowledgeStatus.APPROVED)
                SERVICE.transition(db, admin, item, KnowledgeStatus.ACTIVE)

        available = SERVICE.active_for(db, admin)
        contribution = ARM.select("ما قيمة الغرامة؟", available)

        check(contribution.facts == [], "neither is offered as a fact")
        check(len(contribution.rules) == 1, "the rule is carried as a directive")
        check(len(contribution.preferences) == 1, "the preference is carried as a directive")
        check(
            ARM.render_facts(contribution) == "",
            "the evidence section stays empty, so nothing is presented as a fact",
        )
        directives = ARM.render_directives(contribution)
        check("=== قواعد معتمدة ===" in directives, "rules get their own labelled section")
        check("=== تفضيلات الأسلوب ===" in directives, "preferences get a separate one")
        check(
            "لا تُسقط أي معلومة" in directives,
            "a style preference is explicitly barred from dropping information",
        )
        db.rollback()


# -- 10. a disagreement is shown, not settled -----------------------------
def conflicts_are_surfaced() -> None:
    print("\n-- 10. taught knowledge that contradicts a document raises a conflict --")
    with SessionLocal() as db:
        admin = make_user(db, Role.ADMIN)
        item = SERVICE.propose(
            db, admin,
            ProposedKnowledge(
                type=KnowledgeType.FACT,
                content="قيمة غرامة التأخير القصوى 300,000 درهم",
                scope=KnowledgeScope.GLOBAL,
                source_text="قرار الإدارة",
            ),
        )
        SERVICE.transition(db, admin, item, KnowledgeStatus.APPROVED)
        SERVICE.transition(db, admin, item, KnowledgeStatus.ACTIVE)

        available = SERVICE.active_for(db, admin)
        contribution = ARM.select("ما قيمة غرامة التأخير القصوى؟", available)
        check(len(contribution.facts) == 1, "the taught fact is selected for a matching question")

        conflicts = ARM.find_conflicts(
            contribution, [source("قيمة غرامة التأخير القصوى 268,000 درهم معتمدة", citation=2)]
        )
        check(len(conflicts) == 1, "a different number in a matching passage is a conflict")
        described = conflicts[0].describe()
        check("268,000" in described or "268000" in described, "the document value is shown")
        check("300,000" in described or "300000" in described, "the taught value is shown")
        check("[2]" in described, "the document citation is shown")

        rendered = ARM.render_facts(contribution)
        check("تعارضات مرصودة" in rendered, "the conflict gets its own labelled section")
        check("ولا ترجّح واحدة" in rendered, "and the model is told not to pick a side")

        agreeing = ARM.select("ما قيمة غرامة التأخير القصوى؟", available)
        same = ARM.find_conflicts(
            agreeing, [source("قيمة غرامة التأخير القصوى 300,000 درهم", citation=1)]
        )
        check(same == [], "agreement does not raise a conflict")
        db.rollback()


# -- 11. usage is recorded ------------------------------------------------
def usage_is_tracked() -> None:
    print("\n-- 11. what reached an answer is traceable afterwards --")
    with SessionLocal() as db:
        admin = make_user(db, Role.ADMIN)
        item = SERVICE.propose(
            db, admin,
            ProposedKnowledge(
                type=KnowledgeType.FACT,
                content="مدة الإشعار المسبق ثلاثون يومًا",
                scope=KnowledgeScope.GLOBAL,
            ),
        )
        SERVICE.transition(db, admin, item, KnowledgeStatus.APPROVED)
        SERVICE.transition(db, admin, item, KnowledgeStatus.ACTIVE)
        knowledge = next(k for k in SERVICE.active_for(db, admin) if k.item_id == item.id)

        written = SERVICE.record_usage(
            db, [(knowledge, "stated", True)], question="ما مدة الإشعار؟", user_id=admin.id
        )
        db.flush()
        check(written == 1, "one usage row is written")

        usages = SERVICE.usages(db, item.id)
        check(len(usages) == 1, "the usage is readable back")
        check(usages[0].influence == "stated", "how far it got is recorded")
        check(usages[0].conflicted is True, "whether it disagreed with a document is recorded")
        check(usages[0].version_id == knowledge.version_id, "the exact version is recorded")
        check(
            usages[0].question_hash and usages[0].question_hash != "ما مدة الإشعار؟",
            "the question is stored as a digest, not as text",
        )
        db.rollback()


# -- 12. relevance keeps the prompt bounded --------------------------------
def selection_is_bounded() -> None:
    print("\n-- 12. an unrelated item is not attached to every answer --")
    with SessionLocal() as db:
        admin = make_user(db, Role.ADMIN)
        for content in (
            "مواعيد صرف رواتب الموظفين نهاية كل شهر",
            "سياسة السفر تتطلب موافقة مسبقة من المدير",
        ):
            item = SERVICE.propose(
                db, admin,
                ProposedKnowledge(type=KnowledgeType.FACT, content=content, scope=KnowledgeScope.GLOBAL),
            )
            SERVICE.transition(db, admin, item, KnowledgeStatus.APPROVED)
            SERVICE.transition(db, admin, item, KnowledgeStatus.ACTIVE)

        available = SERVICE.active_for(db, admin)
        check(len(available) >= 2, "both items are retrievable")

        unrelated = ARM.select("ما رقم رخصة البناء المعتمدة للمشروع؟", available)
        check(unrelated.facts == [], "an unrelated question attaches no taught facts")

        related = ARM.select("ما سياسة السفر وهل تتطلب موافقة مسبقة؟", available)
        check(len(related.facts) == 1, "a question that overlaps the wording does attach one")
        db.rollback()


# -- 13. editing rights ----------------------------------------------------
def editing_rights() -> None:
    print("\n-- 13. a contributor may correct their own proposal, not a colleague's --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        colleague = make_user(db, Role.CONTRIBUTOR)
        manager = make_user(db, Role.KNOWLEDGE_MANAGER)

        item = SERVICE.propose(
            db, author,
            ProposedKnowledge(type=KnowledgeType.FACT, content="مسودة", scope=KnowledgeScope.GLOBAL),
        )
        check(SERVICE.may_edit(author, item), "the author may edit their own")
        check(not SERVICE.may_edit(colleague, item), "a colleague may not")
        check(SERVICE.may_edit(manager, item), "a knowledge manager may")

        raised = False
        try:
            SERVICE.edit(db, colleague, item, content="محاولة")
        except AuthorizationError:
            raised = True
        check(raised, "the attempt is refused by the service")
        db.rollback()


if __name__ == "__main__":
    setup_database()

    unapproved_never_retrievable()
    rejected_and_archived_excluded()
    scope_isolation()
    approval_authority()
    versioning_is_append_only()
    transitions_are_constrained()
    source_attribution()
    arm_is_neutral_when_empty()
    directives_are_not_evidence()
    conflicts_are_surfaced()
    usage_is_tracked()
    selection_is_bounded()
    editing_rights()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All knowledge-layer checks passed.")
