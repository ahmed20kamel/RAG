"""Chat endpoint over the knowledge base."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import ContainerDep, RagServiceDep, SessionDep, require
from app.core.knowledge import KnowledgeType
from app.core.permissions import Permission
from app.exceptions import TooManyQuestionsError
from app.models.auth import User
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.query_analysis import NORMALISED_INTERROGATIVES
from app.services.query_rewrite import interpretation_offered, term_statement
from app.services.signal_detector import LearningSignal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


class AnswerFeedback(BaseModel):
    answer_id: str = Field(min_length=1, max_length=36)
    #: "up", "down", or "" to take the thumbs back.
    feedback: Literal["up", "down", ""]


@router.post("/chat/feedback")
def answer_feedback(
    body: AnswerFeedback, service: RagServiceDep, user: User = require(Permission.CHAT_ASK)
) -> dict[str, object]:
    """The reader's thumbs on an answer. A thumbs-down answer is never repeated from
    memory: the same question is answered afresh next time."""
    memory = getattr(service, "question_memory", None)
    if memory is None:
        return {"recorded": False}
    questions = memory.feedback(user, body.answer_id, body.feedback)
    if body.feedback == "down" and getattr(service, "answer_memory", None) is not None:
        for question in questions:
            service.answer_memory.forget(user, question)
    return {"recorded": bool(questions)}


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    service: RagServiceDep,
    session: SessionDep,
    container: ContainerDep,
    user: User = require(Permission.CHAT_ASK),
) -> ChatResponse:
    logger.info("Chat question from %s (%s chars)", user.email, len(request.question))
    admit(user)
    response = service.answer(request, user=user)
    _offer_learning(session, container, user, request, response)
    return response


#: Questions one person may have in progress at once. Two, not one: stopping a question
#: in the browser does not stop the model, and a reader who stopped one and asked again
#: must not be refused while the abandoned answer finishes.
MAX_QUESTIONS_PER_PERSON = 2


def admit(user: User) -> None:
    """Refuse a question from someone who already has the maximum in progress.

    The model answers one question at a time for everyone; a person sending questions
    faster than they can be answered makes everyone else wait behind them.
    """
    from app.services.activity import ACTIVITY

    if ACTIVITY.running_for(user.id) >= MAX_QUESTIONS_PER_PERSON:
        raise TooManyQuestionsError(
            "لديك سؤالان قيد الإجابة الآن. انتظر انتهاء أحدهما ثم اسأل — "
            "هكذا لا يطول الانتظار عليك ولا على غيرك.",
            retryable=True,
        )


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
    # A question that found nothing, rephrased by the same person into one that found
    # something, teaches that person a synonym — at once, and for them alone.
    try:
        container.rephrase_learner.observe(user, request.question, response)
    except Exception:  # noqa: BLE001 - learning must never fail the answer
        logger.exception("Rephrase learning failed")

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
