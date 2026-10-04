"""The buffer between what someone said and what the system believes.

A candidate has no authority. Detection creates one, a person accepts or dismisses it,
and accepting it produces a PENDING proposal that still has to go through review. There
is no path from a sentence in a chat window to an ACTIVE fact that does not pass a human
twice — once to say "yes, I meant to teach that", and once to say "yes, that is true".

The one exception is the one that costs nothing: a personal preference, which affects
its owner's wording and nobody else's facts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.services.access import sees_all
from app.core.knowledge import KnowledgeScope, KnowledgeType
from app.core.permissions import Permission, permissions_for
from app.exceptions import AuthorizationError, DocumentNotFoundError, ValidationError
from app.models.auth import User
from app.models.knowledge_items import LearningCandidate
from app.services.knowledge_service import KnowledgeService, ProposedKnowledge
from app.services.signal_detector import LearningSignal, SignalDetector

logger = logging.getLogger(__name__)


class CandidateState:
    """Where a suggestion stands. Only `accepted` ever produces a proposal."""

    OFFERED = "offered"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    REJECTED = "rejected"


OPEN_STATES = frozenset({CandidateState.OFFERED})
MAX_OPEN_PER_USER = 50


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CandidateService:
    def __init__(
        self,
        detector: SignalDetector | None = None,
        knowledge: KnowledgeService | None = None,
    ) -> None:
        self.detector = detector or SignalDetector()
        self.knowledge = knowledge or KnowledgeService()

    # -- detection --------------------------------------------------------
    def detect(self, message: str) -> LearningSignal | None:
        """Pattern matching only. No model is called, and nothing is written."""
        return self.detector.detect(message)

    def offer(
        self,
        db: DbSession,
        user: User,
        signal: LearningSignal,
        *,
        conversation_id: str = "",
        answer_id: str = "",
        corrects_item_id: str | None = None,
    ) -> LearningCandidate:
        """Records a suggestion for the person to accept or dismiss.

        Writing this row is not a step towards believing the claim. It is the opposite:
        somewhere for the sentence to wait that is not the knowledge base.
        """
        open_count = db.scalar(
            select(func.count())
            .select_from(LearningCandidate)
            .where(
                LearningCandidate.user_id == user.id,
                LearningCandidate.state.in_(list(OPEN_STATES)),
            )
        ) or 0
        if open_count >= MAX_OPEN_PER_USER:
            # A backlog nobody is clearing is noise, not a queue.
            raise ValidationError(
                f"لديك {open_count} اقتراحًا معلّقًا. راجعها قبل إضافة المزيد."
            )

        candidate = LearningCandidate(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id[:64],
            user_id=user.id,
            detected_type=signal.type,
            raw_text=signal.raw_text,
            suggested_content=signal.suggested_content,
            confidence=signal.confidence,
            state=CandidateState.OFFERED,
            signal=signal.signal,
            answer_id=answer_id,
            corrects_item_id=corrects_item_id,
            # A suggestion starts at the narrowest reach that makes sense. Widening it
            # is a decision someone takes on purpose, not a default they inherit.
            # A fact from someone who sees only their own documents is about their own
            # documents: proposed for everyone, one approval would put it in other
            # people's answers. It starts as theirs; a reviewer can still widen it.
            proposed_scope=(
                KnowledgeScope.USER
                if signal.type is KnowledgeType.PREFERENCE or not sees_all(user)
                else KnowledgeScope.GLOBAL
            ),
        )
        db.add(candidate)
        db.flush()
        logger.info(
            "Learning candidate offered: %s (%s/%s) to %s",
            candidate.id, signal.signal, signal.type, user.email,
        )
        return candidate

    # -- authority --------------------------------------------------------
    @staticmethod
    def may_resolve(user: User, candidate: LearningCandidate) -> bool:
        """A suggestion belongs to the person it was offered to.

        Accepting one speaks in their name — the proposal that results is attributed to
        them — so nobody else may accept it. A knowledge manager can dismiss a stale
        queue, but cannot turn someone else's sentence into their proposal.
        """
        if candidate.user_id == user.id:
            return True
        return Permission.USER_MANAGE in permissions_for(user.role)

    @staticmethod
    def get(db: DbSession, candidate_id: str) -> LearningCandidate:
        candidate = db.get(LearningCandidate, candidate_id)
        if candidate is None:
            raise DocumentNotFoundError(f"لا يوجد اقتراح بالمعرّف '{candidate_id}'.")
        return candidate

    # -- resolution -------------------------------------------------------
    def accept(
        self,
        db: DbSession,
        user: User,
        candidate: LearningCandidate,
        *,
        content: str | None = None,
        knowledge_type: KnowledgeType | None = None,
        scope: KnowledgeScope | None = None,
        source_text: str = "",
        explanation: str = "",
        tags: tuple[str, ...] = (),
    ) -> tuple[LearningCandidate, object]:
        """Turns a suggestion into a proposal — not into knowledge.

        The person may correct the wording, the type and the reach before accepting;
        what they cannot do is skip the review that follows. The only thing that becomes
        usable immediately is a personal preference, and only for them.
        """
        if not self.may_resolve(user, candidate):
            raise AuthorizationError("هذا الاقتراح يخص مستخدمًا آخر.")
        if candidate.state != CandidateState.OFFERED:
            raise ValidationError(f"هذا الاقتراح في حالة '{candidate.state}' ولا يمكن قبوله.")
        if Permission.KNOWLEDGE_PROPOSE not in permissions_for(user.role):
            raise AuthorizationError("لا تملك صلاحية اقتراح معرفة.")

        final_type = knowledge_type or KnowledgeType(candidate.detected_type)
        final_scope = scope or KnowledgeScope(candidate.proposed_scope)
        final_content = (content or candidate.suggested_content).strip()
        if not final_content:
            raise ValidationError("محتوى الاقتراح مطلوب.")

        item = self.knowledge.propose(
            db,
            user,
            ProposedKnowledge(
                type=final_type,
                content=final_content,
                scope=final_scope,
                source_text=source_text.strip(),
                confidence=candidate.confidence,
                explanation=explanation.strip(),
                tags=tags,
            ),
        )

        candidate.state = CandidateState.ACCEPTED
        candidate.promoted_item_id = item.id
        candidate.resolved_at = _utcnow()
        candidate.resolved_by = user.id
        db.flush()

        logger.info(
            "Candidate %s accepted by %s -> item %s (%s)",
            candidate.id, user.email, item.id, item.status,
        )
        return candidate, item

    def dismiss(
        self,
        db: DbSession,
        user: User,
        candidate: LearningCandidate,
        *,
        reason: str = "",
        rejected: bool = False,
    ) -> LearningCandidate:
        """Closes a suggestion without creating anything.

        Kept rather than deleted: what the system offered and what a person declined is
        the record that shows detection is not quietly teaching anyone anything.
        """
        if not self.may_resolve(user, candidate):
            raise AuthorizationError("هذا الاقتراح يخص مستخدمًا آخر.")
        if candidate.state != CandidateState.OFFERED:
            raise ValidationError(f"هذا الاقتراح في حالة '{candidate.state}' بالفعل.")

        candidate.state = CandidateState.REJECTED if rejected else CandidateState.DISMISSED
        candidate.resolution_reason = reason.strip()
        candidate.resolved_at = _utcnow()
        candidate.resolved_by = user.id
        db.flush()
        logger.info("Candidate %s %s by %s", candidate.id, candidate.state, user.email)
        return candidate

    # -- reading ----------------------------------------------------------
    @staticmethod
    def search(
        db: DbSession,
        user: User,
        *,
        states: list[str] | None = None,
        types: list[str] | None = None,
        mine_only: bool = True,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[int, list[LearningCandidate]]:
        """Suggestions this person may see.

        A candidate quotes what someone typed, so it is private to them by default. Only
        an administrator may look across the queue, and that is a deliberate capability
        rather than a side effect of seniority.
        """
        filters = []
        if states:
            filters.append(LearningCandidate.state.in_(states))
        if types:
            filters.append(LearningCandidate.detected_type.in_(types))
        if mine_only or Permission.USER_MANAGE not in permissions_for(user.role):
            filters.append(LearningCandidate.user_id == user.id)

        total = db.scalar(
            select(func.count()).select_from(LearningCandidate).where(*filters)
        ) or 0
        rows = list(
            db.scalars(
                select(LearningCandidate)
                .where(*filters)
                .order_by(LearningCandidate.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return total, rows
