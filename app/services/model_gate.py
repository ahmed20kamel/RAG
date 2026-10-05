"""The queue for the model, kept here rather than inside the model server.

The model answers one request at a time. Requests sent to it together wait inside it,
where nothing can take them back out: a reader who closed the page or pressed "stop"
still had their question answered, minutes later, by a model everyone else was waiting
for — and the wait counted against the 300-second limit of the request itself, so a busy
moment turned queued questions into errors.

Waiting happens here instead, first come first served. A question whose reader has gone
leaves the queue before it reaches the model, and a request that reaches the model is
timed from when its turn starts.
"""

from __future__ import annotations

import threading
from collections import deque

from app.exceptions import RagError


class AbandonedError(RagError):
    """The reader left before the model's turn came; nothing was generated."""

    status_code = 499


#: The current request's "the reader has gone" flag, set by the route that serves it.
_current = threading.local()


def watch(event: threading.Event | None) -> None:
    """Tie this thread's model calls to `event`: once it is set, they leave the queue."""
    _current.event = event


def abandoned() -> bool:
    event = getattr(_current, "event", None)
    return event is not None and event.is_set()


class ModelGate:
    """One request at a time, in order of arrival."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiting: deque[object] = deque()
        self._busy = False

    @property
    def waiting(self) -> int:
        with self._condition:
            return len(self._waiting)

    def acquire(self) -> None:
        me = object()
        with self._condition:
            self._waiting.append(me)
            try:
                while self._busy or self._waiting[0] is not me:
                    if abandoned():
                        raise AbandonedError("انصرف السائل قبل دوره؛ لم يُولَّد شيء.")
                    self._condition.wait(timeout=1.0)
            except BaseException:
                self._waiting.remove(me)
                self._condition.notify_all()
                raise
            self._waiting.popleft()
            self._busy = True

    def release(self) -> None:
        with self._condition:
            self._busy = False
            self._condition.notify_all()


class GatedLLM:
    """The model client, behind the gate. Everything but `chat` passes straight through,
    so whatever reads `.model` or `.health()` sees the client it always saw."""

    def __init__(self, inner, gate: ModelGate) -> None:
        self._inner = inner
        self._gate = gate

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if abandoned():
            raise AbandonedError("انصرف السائل؛ لم يُولَّد شيء.")
        self._gate.acquire()
        try:
            return self._inner.chat(system_prompt, user_prompt)
        finally:
            self._gate.release()


#: The one gate every model call from this server passes through.
MODEL_GATE = ModelGate()
