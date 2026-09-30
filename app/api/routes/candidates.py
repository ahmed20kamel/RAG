"""Learning candidates: review what the system thought it heard, and decide."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import ContainerDep, CurrentUserDep, SessionDep, require
from app.core.knowledge import KnowledgeScope, KnowledgeType
from app.core.permissions import Permission
from app.exceptions import AuthorizationError, DocumentNotFoundError
from app.models.answer_trace import AnswerTrace
from app.models.auth import User
from app.models.knowledge_items import LearningCandidate
from app.schemas.candidates import (
    CandidateAcceptRequest,
    CandidateDismissRequest,
    CandidateListResponse,
    CandidateResponse,
    CandidateStatsResponse,
    CorrectAnswerRequest,
)
from app.schemas.knowledge import KnowledgeItemResponse
from app.services.candidate_service import CandidateState
from app.services.signal_detector import LearningSignal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


def _present(session, service, candidate: LearningCandidate, viewer: User) -> CandidateResponse:
    payload = CandidateResponse.model_validate(candidate)
    payload.can_resolve = service.may_resolve(viewer, candidate)
    if candidate.user_id:
        owner = session.get(User, candidate.user_id)
        payload.proposer_name = (owner.display_name or owner.email) if owner else ""
    return payload


@router.get("", response_model=CandidateListResponse)
def list_candidates(
    session: SessionDep,
    container: ContainerDep,
    user: CurrentUserDep,
    state: list[str] | None = Query(default=None),
    type: list[str] | None = Query(default=None),
    mine: bool = Query(default=True),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> CandidateListResponse:
    service = container.candidate_service
    total, rows = service.search(
        session, user, states=state, types=type, mine_only=mine, limit=limit, offset=offset
    )
    return CandidateListResponse(
        total=total, items=[_present(session, service, row, user) for row in rows]
    )


@router.get("/stats", response_model=CandidateStatsResponse)
def candidate_stats(
    session: SessionDep, container: ContainerDep, user: CurrentUserDep
) -> CandidateStatsResponse:
    del container
    mine = LearningCandidate.user_id == user.id

    def tally(column) -> dict[str, int]:
        return {
            str(value): int(count)
            for value, count in session.execute(
                select(column, func.count()).where(mine).group_by(column)
            )
        }

    by_state = tally(LearningCandidate.state)
    return CandidateStatsResponse(
        total=session.scalar(select(func.count()).select_from(LearningCandidate).where(mine)) or 0,
        by_state=by_state,
        by_type=tally(LearningCandidate.detected_type),
        open_for_me=by_state.get(CandidateState.OFFERED, 0),
    )


@router.get("/{candidate_id}", response_model=CandidateResponse)
def get_candidate(
    candidate_id: str, session: SessionDep, container: ContainerDep, user: CurrentUserDep
) -> CandidateResponse:
    service = container.candidate_service
    candidate = service.get(session, candidate_id)
    # A candidate quotes what somebody typed, so it is theirs to read.
    if not service.may_resolve(user, candidate):
        raise AuthorizationError("هذا الاقتراح يخص مستخدمًا آخر.")
    return _present(session, service, candidate, user)


@router.post("/{candidate_id}/accept", response_model=KnowledgeItemResponse, status_code=201)
def accept_candidate(
    candidate_id: str,
    body: CandidateAcceptRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_PROPOSE),
) -> KnowledgeItemResponse:
    """Turns a suggestion into a PENDING proposal. It does not make anything true."""
    from app.api.routes.knowledge import _present as present_item

    service = container.candidate_service
    candidate = service.get(session, candidate_id)
    _resolved, item = service.accept(
        session,
        user,
        candidate,
        content=body.content,
        knowledge_type=body.type,
        scope=body.scope,
        source_text=body.source_text,
        explanation=body.explanation,
        tags=tuple(body.tags),
    )
    session.commit()
    return present_item(session, container.knowledge_service, item, user)


@router.post("/{candidate_id}/dismiss", response_model=CandidateResponse)
def dismiss_candidate(
    candidate_id: str,
    body: CandidateDismissRequest,
    session: SessionDep,
    container: ContainerDep,
    user: CurrentUserDep,
) -> CandidateResponse:
    """Closes a suggestion without creating anything. The row is kept for the record."""
    service = container.candidate_service
    candidate = service.dismiss(
        session, user, service.get(session, candidate_id),
        reason=body.reason, rejected=body.rejected,
    )
    session.commit()
    return _present(session, service, candidate, user)


@router.post("/correct-answer", response_model=CandidateResponse, status_code=201)
def correct_answer(
    body: CorrectAnswerRequest,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.KNOWLEDGE_PROPOSE),
) -> CandidateResponse:
    """Raises a correction against an answer.

    Creates a candidate and nothing else. The answer stays as it was, any knowledge it
    used stays active, and the correction has to be accepted and then approved before it
    can affect anything — which is what keeps "this is wrong" from being self-executing.
    """
    trace = session.scalar(select(AnswerTrace).where(AnswerTrace.id == body.answer_id))
    if trace is None:
        raise DocumentNotFoundError(f"لا يوجد سجل لإجابة بالمعرّف '{body.answer_id}'.")
    if trace.user_id and trace.user_id != user.id:
        raise AuthorizationError("هذه الإجابة تخص مستخدمًا آخر.")

    service = container.candidate_service
    signal = LearningSignal(
        type=KnowledgeType.CORRECTION,
        signal="explicit_correction",
        suggested_content=body.correction.strip(),
        raw_text=body.correction.strip(),
        # Stated outright rather than inferred from phrasing, so it ranks above a
        # detected one — but it still grants nothing.
        confidence=0.9,
    )
    candidate = service.offer(
        session,
        user,
        signal,
        answer_id=body.answer_id,
        corrects_item_id=body.corrects_item_id,
    )
    candidate.proposed_scope = KnowledgeScope.GLOBAL
    session.commit()
    logger.info("Correction raised by %s against answer %s", user.email, body.answer_id)
    return _present(session, service, candidate, user)
