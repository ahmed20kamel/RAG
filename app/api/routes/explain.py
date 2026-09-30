"""Why this answer: the evidence and decisions behind one reply.

Everything returned here is a record of what the answer was assembled from — passages,
approved claims, rules that applied, conflicts and how each was settled. No reasoning is
stored or reported: an explanation made of sources can be checked against those sources,
while a narrated rationale can only be believed.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUserDep, SessionDep
from app.core.permissions import Permission, permissions_for
from app.exceptions import AuthorizationError, DocumentNotFoundError
from app.models.answer_trace import AnswerTrace
from app.schemas.chat import AnswerExplanation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/answers/{answer_id}/why", response_model=AnswerExplanation)
def explain_answer(
    answer_id: str, session: SessionDep, user: CurrentUserDep
) -> AnswerExplanation:
    trace = session.scalar(select(AnswerTrace).where(AnswerTrace.id == answer_id))
    if trace is None:
        raise DocumentNotFoundError(f"لا يوجد سجل لإجابة بالمعرّف '{answer_id}'.")

    # An answer's trace carries the question someone asked. It belongs to them, and to
    # whoever administers the system — not to every colleague with the link.
    if trace.user_id != user.id and Permission.USER_MANAGE not in permissions_for(user.role):
        raise AuthorizationError("هذا السجل يخص مستخدمًا آخر.")

    return AnswerExplanation(
        answer_id=trace.id,
        question=trace.question,
        model=trace.model,
        created_at=trace.created_at,
        document_evidence=list(trace.document_evidence or []),
        knowledge_used=list(trace.knowledge_used or []),
        policies_applied=list(trace.policies_applied or []),
        conflicts=list(trace.conflicts or []),
        conflict_count=trace.conflict_count,
        unresolved_conflicts=trace.unresolved_conflicts,
        knowledge_layer_enabled=trace.knowledge_layer_enabled,
    )
