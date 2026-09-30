"""Chat endpoint over the knowledge base."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.deps import ContainerDep, RagServiceDep, SessionDep, require
from app.core.knowledge import KnowledgeType
from app.core.permissions import Permission
from app.models.auth import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.query_analysis import NORMALISED_INTERROGATIVES
from app.services.query_rewrite import interpretation_offered, term_statement
from app.services.signal_detector import LearningSignal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    service: RagServiceDep,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.CHAT_ASK),
) -> ChatResponse:
    logger.info("Chat question from %s (%s chars)", user.email, len(request.question))
    response = service.answer(request, user=user)
    _offer_learning(session, container, user, request, response)
    return response


def _clarification_signal(container, request, response) -> LearningSignal | None:
    """A terminology suggestion from an interpretation the system just offered.

    When the question used a word the documents never do and the clarification pass
    answered "إن كنت تقصد …", the pair — the word asked, the wording the files use — is
    exactly what a synonym is. It is offered, not applied: it becomes part of search only
    once a person accepts it and a reviewer approves it, like any other taught knowledge.

    Offered only when the question has one or two words the index has never seen. With
    more, which of them the interpretation replaced is a guess, and a wrong synonym would
    widen every later search.
    """
    if response.grounded or response.answer_source in ("none", "web"):
        return None
    meant = interpretation_offered(response.answer)
    if not meant:
        return None
    analysis = container.query_analyzer.analyze(request.question)
    unknown = [
        word for word in container.keyword_index.unknown_terms(analysis.search_text)
        if word not in NORMALISED_INTERROGATIVES
    ]
    if not 1 <= len(unknown) <= 2:
        return None
    asked = " ".join(unknown)
    if asked == meant:
        return None
    return LearningSignal(
        type=KnowledgeType.TERMINOLOGY,
        signal="clarification",
        suggested_content=term_statement(asked, meant),
        raw_text=request.question,
        confidence=0.4,
    )


def _offer_learning(session, container, user, request, response) -> None:
    """Notices teaching intent in the question and offers to record it.

    Runs on the message the person typed, never on the answer, and never calls a model —
    detection is pattern matching over the same normalised Arabic the rest of the system
    uses. It writes one row the person can accept or dismiss, and changes nothing about
    the answer they are reading.

    Failing here must not fail the answer: a suggestion is a convenience, the reply is
    the thing they asked for.
    """
    settings = container.settings
    if not settings.enable_learning_signals:
        return
    try:
        signal = container.candidate_service.detect(request.question)
        if signal is None:
            signal = _clarification_signal(container, request, response)
        if signal is None:
            return
        candidate = container.candidate_service.offer(
            session,
            user,
            signal,
            answer_id=response.answer_id,
        )
        session.commit()
        response.learning_signal = {
            "candidate_id": candidate.id,
            "type": str(signal.type),
            "signal": signal.signal,
            "suggested_content": signal.suggested_content,
            "confidence": signal.confidence,
            "proposed_scope": candidate.proposed_scope,
            "needs_approval": signal.needs_approval,
        }
    except Exception:  # noqa: BLE001
        logger.exception("Could not offer a learning suggestion")
