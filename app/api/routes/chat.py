"""Chat endpoint over the knowledge base."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, File, Form, UploadFile
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


class ExportRequest(BaseModel):
    """An answer already on the reader's screen, to be delivered as a file."""

    format: Literal["pdf", "docx", "xlsx"]
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1, max_length=60000)
    sources: list[dict] = Field(default_factory=list, max_length=60)


@router.post("/chat/export")
def export_answer(body: ExportRequest, container: ContainerDep, user: User = require(Permission.CHAT_ASK)):
    """One click under an answer: the same template as a file asked for in words. The
    figures are checked against the passages the answer cited, read again from the index
    by their ids — not against anything the request itself claims."""
    from app.schemas.chat import SourceReference
    from app.services.file_export import FileRequest

    sources = []
    for raw in body.sources:
        try:
            sources.append(SourceReference.model_validate(raw))
        except Exception:  # noqa: BLE001 - a malformed source is left out, not fatal
            continue
    reply = container.exports._deliver(
        FileRequest(body.format, "last"), user, title=body.title, markdown=body.markdown, sources=sources,
    )
    return {"files": [f.model_dump() for f in reply.files], "note": reply.answer}


@router.get("/chat/files/{file_id}")
def download_file(file_id: str, container: ContainerDep, user: User = require(Permission.CHAT_ASK)):
    """A file made for this reader. Anyone else's id reads as not found."""
    from urllib.parse import quote

    from fastapi.responses import FileResponse

    stored, path = container.exports.store.open(user.id, file_id)
    return FileResponse(
        path, media_type=stored.media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(stored.name)}"},
    )


@router.post("/chat/picture")
async def read_chat_picture(
    container: ContainerDep,
    file: UploadFile = File(...),
    marks: str = Form(default="[]"),
    _user: User = require(Permission.CHAT_ASK),
) -> dict[str, object]:
    """Read a picture the reader is about to ask about, and the parts they marked on it.

    `marks` is a JSON list of {x, y, w, h}, each a fraction of the picture's size. The
    picture is read and dropped; only its words come back, to be sent with the question.
    """
    import json

    from starlette.concurrency import run_in_threadpool

    from app.exceptions import ValidationError
    from app.services.chat_attachments import Mark, read_picture

    try:
        regions = [Mark(float(m["x"]), float(m["y"]), float(m["w"]), float(m["h"])) for m in json.loads(marks or "[]")]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValidationError("علامات الصورة غير صالحة.") from exc
    data = await file.read()
    settings = container.settings
    vision = (settings.ollama_base_url, settings.vision_model) if settings.vision_enabled else None
    picture = await run_in_threadpool(read_picture, data, regions, vision)
    return {"text": picture.text, "marked": " … ".join(picture.marked), "vision": picture.vision,
            "has_text": picture.has_text}


@router.post("/chat/transcribe")
async def transcribe(
    container: ContainerDep,
    file: UploadFile = File(...),
    _user: User = require(Permission.CHAT_ASK),
) -> dict[str, str]:
    """Speech to text, on this machine. The text comes back to the reader's input box to
    be read and corrected before it is sent — never sent as a question unseen."""
    from starlette.concurrency import run_in_threadpool

    from app.exceptions import IntegrationDisabledError

    if container.speech is None:
        raise IntegrationDisabledError("الإدخال الصوتي غير مفعّل على هذا الخادم.")
    suffix = "." + (file.filename or "audio.webm").rsplit(".", 1)[-1][:5]
    data = await file.read()
    return {"text": await run_in_threadpool(container.speech.transcribe, data, suffix)}


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
    with waiting_on(user):
        response = container.exports.handle(request, user) or service.answer(request, user=user)
    _offer_learning(session, container, user, request, response)
    return response


#: Questions one person may have in progress at once. Two, not one: stopping a question
#: in the browser does not stop the model, and a reader who stopped one and asked again
#: must not be refused while the abandoned answer finishes.
MAX_QUESTIONS_PER_PERSON = 2


#: Questions each person is waiting on with the page still open. Counted by connection,
#: not by work in progress: a question whose page was closed is withdrawn from the
#: model's queue, and must not hold its asker back while that happens.
_OPEN: dict[str, int] = {}
_OPEN_LOCK = threading.Lock()


@contextmanager
def waiting_on(user: User):
    with _OPEN_LOCK:
        _OPEN[user.id] = _OPEN.get(user.id, 0) + 1
    try:
        yield
    finally:
        with _OPEN_LOCK:
            _OPEN[user.id] = max(0, _OPEN.get(user.id, 1) - 1)


def admit(user: User) -> None:
    """Refuse a question from someone already waiting on the maximum.

    The model answers one question at a time for everyone; a person sending questions
    faster than they can be answered makes everyone else wait behind them.
    """
    with _OPEN_LOCK:
        open_now = _OPEN.get(user.id, 0)
    if open_now >= MAX_QUESTIONS_PER_PERSON:
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
