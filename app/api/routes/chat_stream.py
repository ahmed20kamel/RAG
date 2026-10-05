"""Chat with live stage events.

The model itself is not streamed — the RAG pipeline answers in one call and validates
the result afterwards, so there is no partial answer to send. What *can* be reported
truthfully is where the request has got to, and that is taken from the pipeline's own
log records: a stage event is emitted because that stage actually ran, not because a
timer said it should have. Nothing here changes how an answer is produced.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import ContainerDep, CurrentUserDep, RagServiceDep, require
from app.api.routes.chat import _offer_learning, admit, waiting_on
from app.core.permissions import Permission
from app.models.auth import User
from app.models.database import SessionLocal
from app.schemas.chat import ChatRequest
from app.services.activity import ACTIVITY
from app.services.model_gate import AbandonedError, watch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# Which logger reports which stage. Keyed on the module that emitted the record rather
# than on its wording, so a reworded log line cannot silently break the stream.
STAGE_BY_LOGGER: dict[str, str] = {
    "app.services.retriever": "retrieving",
    "app.services.context_builder": "assembling",
    "app.services.fact_sheet": "extracting",
    "app.services.contract_builder": "planning",
    "app.services.evidence_planner": "planning",
    "app.services.coverage": "verifying",
    "app.services.completion": "completing",
}
# Generation leaves no log of its own — it is one blocking call — but it always follows
# planning, so the planner's last record marks its start.
STAGE_AFTER: dict[str, str] = {"planning": "generating"}
STAGE_ORDER = (
    "retrieving", "assembling", "extracting", "planning",
    "generating", "verifying", "completing",
)


class _StageCollector(logging.Handler):
    """Turns this request's log records into stage events, ignoring every other thread."""

    def __init__(self, thread_id: int, sink: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        super().__init__(level=logging.INFO)
        self.thread_id = thread_id
        self.sink = sink
        self.loop = loop
        self.seen: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id:
            return
        stage = STAGE_BY_LOGGER.get(record.name)
        if stage is None or stage in self.seen:
            return
        self.seen.add(stage)
        self._push({"type": "stage", "stage": stage})

    def close_stage(self, stage: str) -> None:
        if stage not in self.seen:
            self.seen.add(stage)
            self._push({"type": "stage", "stage": stage})

    def _push(self, event: dict) -> None:
        self.loop.call_soon_threadsafe(self.sink.put_nowait, event)


#: Seconds of silence before a keep-alive line; well under any proxy's idle timeout.
HEARTBEAT_SECONDS = 15


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    service: RagServiceDep,
    container: ContainerDep,
    user: User = require(Permission.CHAT_ASK),
) -> StreamingResponse:
    """Newline-delimited JSON: zero or more stage events, then one result or error."""
    admit(user)
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()
    root = logging.getLogger("app.services")

    # Set when the reader goes — page closed, "stop" pressed, connection lost — so the
    # question leaves the model's queue instead of being answered for nobody.
    gone = threading.Event()

    async def run() -> AsyncIterator[str]:
        collector: _StageCollector | None = None

        def work() -> None:
            nonlocal collector
            collector = _StageCollector(threading.get_ident(), events, loop)
            root.addHandler(collector)
            watch(gone)
            try:
                response = service.answer(request, user=user, channel="stream")
                # The same learning offer the plain route makes. The interface asks through
                # this route, so without it a question that taught something — or an
                # interpretation worth keeping — was never offered to the person at all.
                with SessionLocal() as session:
                    _offer_learning(session, container, user, request, response)
            except AbandonedError:
                logger.info("Question withdrawn: %s left before the model's turn", user.email)
            except Exception as exc:  # surfaced to the client, then re-raised into the log
                logger.exception("Streaming chat failed")
                loop.call_soon_threadsafe(
                    events.put_nowait,
                    {"type": "error", "detail": f"تعذّر إنتاج الإجابة: {exc}"},
                )
            else:
                loop.call_soon_threadsafe(
                    events.put_nowait,
                    {"type": "result", "response": response.model_dump(mode="json")},
                )
            finally:
                watch(None)
                root.removeHandler(collector)
                loop.call_soon_threadsafe(events.put_nowait, None)

        # The questions already being answered when this one arrived are the ones it
        # waits behind: the model answers one at a time. Said up front, and again as
        # each of them finishes, so a busy moment reads as a queue, not a hang.
        ahead_of = ACTIVITY.snapshot()

        def load(ahead: int) -> str:
            return json.dumps({
                "type": "load", "ahead": ahead, "wait_seconds": ACTIVITY.estimate_wait(ahead),
            }) + "\n"

        ahead = len(ahead_of)
        with waiting_on(user):
            try:
                yield load(ahead)
                worker = loop.run_in_executor(None, work)
                while True:
                    try:
                        event = await asyncio.wait_for(events.get(), timeout=HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        # Nothing to report while the model writes, which can take minutes. A
                        # silent connection is dropped by proxies — the public tunnel cuts one
                        # after 100 seconds — and the reader is left waiting on an answer that
                        # can no longer arrive. A line every few seconds keeps it open.
                        now_ahead = ACTIVITY.still_running(ahead_of)
                        if now_ahead != ahead:
                            ahead = now_ahead
                            yield load(ahead)
                        else:
                            yield json.dumps({"type": "ping"}) + "\n"
                        continue
                    if event is None:
                        break
                    # The planner's record is the last thing logged before the model is
                    # called, so once it lands the request is provably in generation.
                    if event.get("stage") == "planning" and collector is not None:
                        yield json.dumps(event, ensure_ascii=False) + "\n"
                        collector.close_stage("generating")
                        continue
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                await worker
            finally:
                # Ended, or the reader left mid-way (the generator is closed): either way
                # nobody is waiting on this question now, and if it has not reached the
                # model yet it leaves the queue.
                gone.set()

    return StreamingResponse(
        run(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/stages")
def chat_stages(_user: CurrentUserDep) -> dict[str, list[str]]:
    """The stage names this backend can report, in the order they occur."""
    return {"stages": list(STAGE_ORDER)}
