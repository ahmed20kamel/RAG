"""Conversational learning, tested against the boundary it exists to protect.

The property under test throughout is that no sentence typed into a chat window becomes
a trusted fact without two separate human acts: accepting the suggestion, and approving
the proposal it produces. Every case here is a phrasing or a lifecycle shape — never a
particular question, document or project.

Deterministic and offline: a temporary SQLite file, no model, no network.

Run: python tests/test_phase4_learning.py
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

_TMP = Path(tempfile.mkdtemp(prefix="rag-phase4-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'phase4.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")

from app.core.knowledge import (  # noqa: E402
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
)
from app.core.permissions import Role  # noqa: E402
from app.exceptions import AuthorizationError, ValidationError  # noqa: E402
from app.models.auth import Team, User  # noqa: E402
from app.models.database import Base, SessionLocal, engine  # noqa: E402
from app.models.knowledge_items import KnowledgeVersion  # noqa: E402
from app.services.candidate_service import CandidateService, CandidateState  # noqa: E402
from app.services.knowledge_service import (  # noqa: E402
    KnowledgeService,
    ProposedKnowledge,
)
from app.services.signal_detector import SignalDetector  # noqa: E402

FAILURES: list[str] = []
DETECTOR = SignalDetector()
KNOWLEDGE = KnowledgeService()
CANDIDATES = CandidateService(detector=DETECTOR, knowledge=KNOWLEDGE)


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def setup_database() -> None:
    from app.models import answer_trace, auth, document, knowledge, knowledge_items  # noqa: F401

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


# -- 1-3. detection: Arabic, English, and questions ------------------------
def arabic_signals_are_detected() -> None:
    print("\n-- 1. Arabic teaching phrasings are recognised --")
    cases = [
        ("تعلم القاعدة: عند تعارض مستندين اعرض القيمتين مع المصدر", KnowledgeType.RULE),
        ("من الآن فصاعدًا اذكر العملة بعد كل مبلغ في الإجابة", KnowledgeType.RULE),
        ("المعلومة السابقة غير صحيحة، الصحيح هو خمسة عشر بالمئة", KnowledgeType.CORRECTION),
        ("يقصد بالمعاينة في مشروعنا زيارة الخبير الميدانية وليس الاجتماع", KnowledgeType.TERMINOLOGY),
        ("أفضّل الإجابات المختصرة بالعربية الفصحى", KnowledgeType.PREFERENCE),
        ("الإجراء هو إشعار التأمين ثم تجهيز المستندات ثم مراجعة المحامي", KnowledgeType.PROCEDURE),
        ("تعلم أن الاسم الرسمي للجهة هو شركة المقاولات العامة المحدودة", KnowledgeType.FACT),
    ]
    for message, expected in cases:
        signal = DETECTOR.detect(message)
        check(
            signal is not None and signal.type is expected,
            f"«{message[:34]}…» → {expected.value}"
            + (f" (got {signal.type.value})" if signal and signal.type is not expected else ""),
        )


def english_signals_are_detected() -> None:
    print("\n-- 2. the same phrasings in English --")
    cases = [
        ("from now on always show both values when two sources disagree", KnowledgeType.RULE),
        ("that is incorrect, the correct value is 268,000 AED", KnowledgeType.CORRECTION),
        ("we mean by handover the formal site acceptance, not the inspection", KnowledgeType.TERMINOLOGY),
        ("I prefer short answers in English please", KnowledgeType.PREFERENCE),
        ("the procedure is notify insurance then prepare the documents", KnowledgeType.PROCEDURE),
        ("learn that the official registered name is Alpha Contracting LLC", KnowledgeType.FACT),
    ]
    for message, expected in cases:
        signal = DETECTOR.detect(message)
        check(
            signal is not None and signal.type is expected,
            f"«{message[:34]}…» → {expected.value}"
            + (f" (got {signal.type.value})" if signal and signal.type is not expected else ""),
        )


def questions_are_not_lessons() -> None:
    print("\n-- 3. asking is not teaching --")
    for message in (
        "ما رقم العقد المعتمد للمشروع؟",
        "من هم أطراف النزاع وما دور كل طرف؟",
        "what is the maximum delay penalty?",
        "هل تعلم القاعدة؟",
        "شكرًا لك",
        "متى بدأ الحفر؟",
    ):
        check(DETECTOR.detect(message) is None, f"«{message[:36]}» raises no suggestion")

    # A cue with nothing after it is a remark, not a lesson.
    check(DETECTOR.detect("الصحيح هو") is None, "a cue with no substance raises nothing")


def extraction_keeps_the_claim_readable() -> None:
    print("\n-- 4. the suggestion is the claim, not the cue --")
    signal = DETECTOR.detect("المعلومة السابقة غير صحيحة، الصحيح هو 15% وليس 10%")
    check(signal is not None, "the correction is detected")
    if signal:
        check(
            "15%" in signal.suggested_content and "غير صحيحة" not in signal.suggested_content,
            f"the lead-in is stripped and the claim kept ({signal.suggested_content})",
        )
        check(signal.raw_text.startswith("المعلومة"), "the original message is kept verbatim")

    diacritics = DETECTOR.detect("أفضّل الإجابات المختصرة بالعربية")
    check(
        diacritics is not None and diacritics.suggested_content.startswith("الإجاب"),
        f"diacritics do not shift the extraction ({diacritics.suggested_content if diacritics else ''})",
    )


# -- 5-8. candidate lifecycle ---------------------------------------------
def detection_writes_nothing_by_itself() -> None:
    print("\n-- 5. detecting a lesson creates no knowledge --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        before = len(KNOWLEDGE.active_for(db, author))

        signal = DETECTOR.detect("تعلم القاعدة: اعرض دائمًا مصدر كل رقم في الإجابة")
        check(signal is not None, "the rule is detected")
        check(
            len(KNOWLEDGE.active_for(db, author)) == before,
            "detection alone adds nothing retrievable",
        )

        candidate = CANDIDATES.offer(db, author, signal)
        db.flush()
        check(candidate.state == CandidateState.OFFERED, "it lands as an OFFERED suggestion")
        check(candidate.promoted_item_id is None, "with no knowledge item behind it")
        check(
            len(KNOWLEDGE.active_for(db, author)) == before,
            "and still nothing is retrievable",
        )
        db.rollback()


def accepting_produces_a_proposal_not_knowledge() -> None:
    print("\n-- 6. accepting a suggestion produces a PENDING proposal --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        signal = DETECTOR.detect(
            "تعلم أن رقم القيد المعتمد للخبير في هذا الملف هو مئة وأربعة وثلاثون"
        )
        candidate = CANDIDATES.offer(db, author, signal)
        _resolved, item = CANDIDATES.accept(db, author, candidate)
        db.flush()

        check(candidate.state == CandidateState.ACCEPTED, "the candidate is marked accepted")
        check(candidate.promoted_item_id == item.id, "and points at what it produced")
        check(item.status == KnowledgeStatus.PENDING, "the proposal is PENDING, not active")
        check(
            not any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, author)),
            "so it reaches no answer yet",
        )
        check(item.created_by == author.id, "the proposal is attributed to the person")
        db.rollback()


def a_preference_activates_only_for_its_owner() -> None:
    print("\n-- 7. a personal preference is the one thing that skips review --")
    with SessionLocal() as db:
        owner = make_user(db, Role.CONTRIBUTOR)
        colleague = make_user(db, Role.CONTRIBUTOR)

        signal = DETECTOR.detect("أفضّل الإجابات المختصرة بالعربية الفصحى")
        check(signal is not None and not signal.needs_approval, "a preference needs no approval")

        candidate = CANDIDATES.offer(db, owner, signal)
        check(
            candidate.proposed_scope == KnowledgeScope.USER,
            "and is proposed at personal scope by default",
        )
        _resolved, item = CANDIDATES.accept(db, owner, candidate)
        db.flush()

        check(item.status == KnowledgeStatus.ACTIVE, "it activates immediately")
        check(
            any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, owner)),
            "for its owner",
        )
        check(
            not any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, colleague)),
            "and for nobody else",
        )
        db.rollback()


def a_fact_never_skips_review() -> None:
    print("\n-- 8. a personal FACT still has to be approved --")
    with SessionLocal() as db:
        owner = make_user(db, Role.CONTRIBUTOR)
        signal = DETECTOR.detect("تعلم أن رقم الحساب المعتمد للمشروع هو 4471 فقط")
        candidate = CANDIDATES.offer(db, owner, signal)
        _resolved, item = CANDIDATES.accept(db, owner, candidate, scope=KnowledgeScope.USER)
        db.flush()
        check(
            item.status == KnowledgeStatus.PENDING,
            "a claim is a claim whatever its reach, so it waits for review",
        )
        db.rollback()


def dismissing_leaves_a_record_and_nothing_else() -> None:
    print("\n-- 9. dismissing closes it without creating anything --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        signal = DETECTOR.detect("من الآن فصاعدًا اذكر اسم القسم في كل إجابة")
        candidate = CANDIDATES.offer(db, author, signal)

        CANDIDATES.dismiss(db, author, candidate, reason="قلتها بالخطأ")
        db.flush()
        check(candidate.state == CandidateState.DISMISSED, "the state becomes dismissed")
        check(candidate.promoted_item_id is None, "nothing was created")
        check(candidate.resolution_reason == "قلتها بالخطأ", "the reason is kept")
        check(candidate.resolved_by == author.id, "and who closed it")

        raised = False
        try:
            CANDIDATES.accept(db, author, candidate)
        except ValidationError:
            raised = True
        check(raised, "a closed suggestion cannot be accepted afterwards")
        db.rollback()


def a_rejected_candidate_reaches_no_answer() -> None:
    print("\n-- 10. a rejected suggestion is excluded from retrieval --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        signal = DETECTOR.detect("تعلم أن قيمة الدفعة المقدمة هي 500000 درهم")
        candidate = CANDIDATES.offer(db, author, signal)
        CANDIDATES.dismiss(db, author, candidate, reason="غير صحيح", rejected=True)
        db.flush()

        check(candidate.state == CandidateState.REJECTED, "the state becomes rejected")
        retrievable = KNOWLEDGE.active_for(db, author)
        check(
            not any(signal.suggested_content in k.content for k in retrievable),
            "its content is nowhere in retrievable knowledge",
        )
        db.rollback()


# -- 11-13. corrections ----------------------------------------------------
def a_correction_does_not_overwrite_anything() -> None:
    print("\n-- 11. a correction leaves the existing knowledge alone --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        original = KNOWLEDGE.propose(
            db, author,
            ProposedKnowledge(
                type=KnowledgeType.FACT, scope=KnowledgeScope.GLOBAL,
                content="نسبة غرامة التأخير عشرة بالمئة", source_text="العقد",
            ),
        )
        KNOWLEDGE.transition(db, admin, original, KnowledgeStatus.APPROVED)
        KNOWLEDGE.transition(db, admin, original, KnowledgeStatus.ACTIVE)
        original_version = original.active_version_id

        signal = DETECTOR.detect("المعلومة السابقة غير صحيحة، الصحيح هو خمسة بالمئة")
        candidate = CANDIDATES.offer(db, author, signal, corrects_item_id=original.id)
        db.flush()

        check(candidate.detected_type == KnowledgeType.CORRECTION, "it is typed as a correction")
        check(candidate.corrects_item_id == original.id, "and linked to what it disputes")
        check(original.status == KnowledgeStatus.ACTIVE, "the existing item stays ACTIVE")
        check(
            original.active_version_id == original_version,
            "its wording is untouched while the correction waits",
        )
        check(
            any(k.item_id == original.id for k in KNOWLEDGE.active_for(db, admin)),
            "and it keeps reaching answers until the correction is approved",
        )
        db.rollback()


def an_accepted_correction_is_still_only_a_proposal() -> None:
    print("\n-- 12. accepting a correction does not apply it --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        original = KNOWLEDGE.propose(
            db, author,
            ProposedKnowledge(
                type=KnowledgeType.FACT, scope=KnowledgeScope.GLOBAL,
                content="مدة الإشعار ثلاثون يومًا", source_text="العقد",
            ),
        )
        KNOWLEDGE.transition(db, admin, original, KnowledgeStatus.APPROVED)
        KNOWLEDGE.transition(db, admin, original, KnowledgeStatus.ACTIVE)

        signal = DETECTOR.detect("المعلومة السابقة غير صحيحة، الصحيح هو خمسة عشر يومًا")
        candidate = CANDIDATES.offer(db, author, signal, corrects_item_id=original.id)
        _resolved, correction = CANDIDATES.accept(db, author, candidate)
        db.flush()

        check(correction.status == KnowledgeStatus.PENDING, "the correction is PENDING")
        check(original.status == KnowledgeStatus.ACTIVE, "the original is still ACTIVE")
        active_ids = {k.item_id for k in KNOWLEDGE.active_for(db, admin)}
        check(original.id in active_ids, "the original still answers")
        check(correction.id not in active_ids, "the correction does not")
        db.rollback()


def old_versions_stay_immutable() -> None:
    print("\n-- 13. an edit appends; what was approved stays readable --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        item = KNOWLEDGE.propose(
            db, author,
            ProposedKnowledge(
                type=KnowledgeType.FACT, scope=KnowledgeScope.GLOBAL, content="النسخة الأولى"
            ),
        )
        KNOWLEDGE.transition(db, admin, item, KnowledgeStatus.APPROVED)
        KNOWLEDGE.transition(db, admin, item, KnowledgeStatus.ACTIVE)
        first = item.active_version_id

        KNOWLEDGE.edit(db, author, item, content="النسخة الثانية", change_reason="تصحيح")
        db.flush()

        check(db.get(KnowledgeVersion, first).content == "النسخة الأولى", "v1 is unchanged")
        check(item.active_version_id != first, "the item points at v2")
        check(item.status == KnowledgeStatus.PENDING, "and the edit withdrew the approval")
        check(len(KNOWLEDGE.versions(db, item.id)) == 2, "both versions are kept")
        db.rollback()


# -- 14-17. authority and privacy -----------------------------------------
def a_suggestion_belongs_to_the_person_it_was_offered_to() -> None:
    print("\n-- 14. nobody may accept somebody else's suggestion --")
    with SessionLocal() as db:
        owner = make_user(db, Role.CONTRIBUTOR)
        colleague = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        signal = DETECTOR.detect("تعلم أن رقم المرجع المعتمد هو 7781 دائمًا")
        candidate = CANDIDATES.offer(db, owner, signal)

        check(CANDIDATES.may_resolve(owner, candidate), "the owner may resolve it")
        check(not CANDIDATES.may_resolve(colleague, candidate), "a colleague may not")
        check(CANDIDATES.may_resolve(admin, candidate), "an administrator may")

        raised = False
        try:
            CANDIDATES.accept(db, colleague, candidate)
        except AuthorizationError:
            raised = True
        check(raised, "the attempt is refused by the service, not just hidden")
        db.rollback()


def a_viewer_cannot_turn_a_suggestion_into_a_proposal() -> None:
    print("\n-- 15. proposing needs the capability, even from a suggestion --")
    with SessionLocal() as db:
        viewer = make_user(db, Role.VIEWER)
        signal = DETECTOR.detect("تعلم أن الرقم المعتمد للمشروع هو 3321 فقط")
        candidate = CANDIDATES.offer(db, viewer, signal)

        raised = False
        try:
            CANDIDATES.accept(db, viewer, candidate)
        except AuthorizationError:
            raised = True
        check(raised, "a viewer cannot promote a suggestion into a proposal")
        check(candidate.state == CandidateState.OFFERED, "and the suggestion is left open")
        db.rollback()


def suggestions_are_private_to_their_owner() -> None:
    print("\n-- 16. a suggestion quotes what someone typed, so it stays theirs --")
    with SessionLocal() as db:
        alice = make_user(db, Role.CONTRIBUTOR)
        bob = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        signal = DETECTOR.detect("أفضّل الإجابات المطوّلة مع كل التفاصيل الممكنة")
        CANDIDATES.offer(db, alice, signal)
        db.flush()

        _total_a, mine = CANDIDATES.search(db, alice, mine_only=True)
        check(len(mine) == 1, "the owner sees their own suggestion")

        _total_b, theirs = CANDIDATES.search(db, bob, mine_only=False)
        check(len(theirs) == 0, "a colleague sees nothing, even asking for everything")

        _total_c, all_of_them = CANDIDATES.search(db, admin, mine_only=False)
        check(len(all_of_them) >= 1, "an administrator may review the queue")
        db.rollback()


def scope_isolation_survives_the_learning_path() -> None:
    print("\n-- 17. knowledge taught in conversation obeys the same scope rules --")
    with SessionLocal() as db:
        team_a, team_b = make_team(db, "a"), make_team(db, "b")
        alice = make_user(db, Role.CONTRIBUTOR, team_id=team_a.id)
        bob = make_user(db, Role.CONTRIBUTOR, team_id=team_b.id)
        admin = make_user(db, Role.ADMIN)

        signal = DETECTOR.detect("تعلم أن نموذج التسليم المعتمد لدينا هو النموذج الثاني")
        candidate = CANDIDATES.offer(db, alice, signal)
        _resolved, item = CANDIDATES.accept(db, alice, candidate, scope=KnowledgeScope.TEAM)
        KNOWLEDGE.transition(db, admin, item, KnowledgeStatus.APPROVED)
        KNOWLEDGE.transition(db, admin, item, KnowledgeStatus.ACTIVE)
        db.flush()

        check(
            any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, alice)),
            "her team sees it",
        )
        check(
            not any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, bob)),
            "another team does not",
        )
        db.rollback()


# -- 18-20. guarantees that must not erode ---------------------------------
def a_preference_cannot_override_evidence() -> None:
    print("\n-- 18. a preference touches wording, never content --")
    from app.services.knowledge_arm import KnowledgeArm

    with SessionLocal() as db:
        owner = make_user(db, Role.CONTRIBUTOR)
        signal = DETECTOR.detect("أفضّل الإجابات المختصرة جدًا بدون تفاصيل")
        candidate = CANDIDATES.offer(db, owner, signal)
        CANDIDATES.accept(db, owner, candidate)
        db.flush()

        contribution = KnowledgeArm().select("ما قيمة العقد؟", KNOWLEDGE.active_for(db, owner))
        check(contribution.facts == [], "a preference is never offered as a fact")
        check(len(contribution.preferences) == 1, "it is carried as a directive instead")
        check(
            KnowledgeArm.render_facts(contribution) == "",
            "the evidence section stays empty",
        )
        directives = KnowledgeArm.render_directives(contribution)
        check(
            "لا تُسقط أي معلومة" in directives,
            "and the directive is explicitly barred from dropping information",
        )
        db.rollback()


def the_full_path_still_ends_at_a_reviewer() -> None:
    print("\n-- 19. message → suggestion → proposal → review → use --")
    with SessionLocal() as db:
        author = make_user(db, Role.CONTRIBUTOR)
        admin = make_user(db, Role.ADMIN)

        signal = DETECTOR.detect(
            "تعلم القاعدة: عند تعارض مستندين اعرض القيمتين مع مصدر كل واحدة"
        )
        candidate = CANDIDATES.offer(db, author, signal)
        check(
            not any(signal.suggested_content in k.content for k in KNOWLEDGE.active_for(db, admin)),
            "step 1: a detected lesson is not usable",
        )

        _resolved, item = CANDIDATES.accept(db, author, candidate)
        db.flush()
        check(
            not any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, admin)),
            "step 2: an accepted suggestion is not usable",
        )

        KNOWLEDGE.transition(db, admin, item, KnowledgeStatus.APPROVED)
        check(
            not any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, admin)),
            "step 3: an approved proposal is not yet usable",
        )

        KNOWLEDGE.transition(db, admin, item, KnowledgeStatus.ACTIVE)
        db.flush()
        check(
            any(k.item_id == item.id for k in KNOWLEDGE.active_for(db, admin)),
            "step 4: only activation makes it usable",
        )

        trail = [r.decision for r in KNOWLEDGE.reviews(db, item.id)]
        check(
            trail == ["submitted", "approved", "activated"],
            f"and every step is on the record ({trail})",
        )
        db.rollback()


def detection_never_calls_a_model() -> None:
    print("\n-- 20. detection is pattern matching, not generation --")
    import inspect

    from app.services import signal_detector

    source = inspect.getsource(signal_detector)
    for forbidden in ("llm", "chat(", "embed", "openai", "ollama", "generate"):
        check(
            forbidden not in source.lower().replace("matching", ""),
            f"the detector contains no '{forbidden}' call",
        )

    # And it is fast enough to run on every message without being noticed.
    import time

    message = "تعلم القاعدة: اعرض دائمًا مصدر كل رقم مذكور في الإجابة النهائية"
    start = time.perf_counter()
    for _ in range(200):
        DETECTOR.detect(message)
    per_call = (time.perf_counter() - start) * 1000 / 200
    check(per_call < 5.0, f"detection costs {per_call:.2f} ms per message")


if __name__ == "__main__":
    setup_database()

    arabic_signals_are_detected()
    english_signals_are_detected()
    questions_are_not_lessons()
    extraction_keeps_the_claim_readable()
    detection_writes_nothing_by_itself()
    accepting_produces_a_proposal_not_knowledge()
    a_preference_activates_only_for_its_owner()
    a_fact_never_skips_review()
    dismissing_leaves_a_record_and_nothing_else()
    a_rejected_candidate_reaches_no_answer()
    a_correction_does_not_overwrite_anything()
    an_accepted_correction_is_still_only_a_proposal()
    old_versions_stay_immutable()
    a_suggestion_belongs_to_the_person_it_was_offered_to()
    a_viewer_cannot_turn_a_suggestion_into_a_proposal()
    suggestions_are_private_to_their_owner()
    scope_isolation_survives_the_learning_path()
    a_preference_cannot_override_evidence()
    the_full_path_still_ends_at_a_reviewer()
    detection_never_calls_a_model()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All Phase 4 learning checks passed.")
