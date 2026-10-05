"""Whether someone is waiting on an answer right now — and how long a newcomer will wait.

There is one graphics card. A question someone is waiting for and a background job —
sorting an upload into its folder — both need the model, and the model serves one at a
time. Background work asks here first and waits for a quiet moment, so a person never
queues behind a filing job.

The same count tells a person who asks at a busy moment where they stand: the questions
that were already being answered when theirs arrived are the ones ahead of it, and the
time recent questions took — measured, not assumed — says roughly how long that is.
"""

from __future__ import annotations

import itertools
import statistics
import threading
import time
from collections import deque

#: Assumed until a question has been timed on this machine.
DEFAULT_ANSWER_SECONDS = 60.0


class Activity:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[int, tuple[float, bool]] = {}
        self._owners: dict[int, str] = {}
        self._tickets = itertools.count(1)
        self._last_end = 0.0
        # Only answers that ran alone: one that waited behind others would count the
        # wait as its own time, and every estimate built on it would compound.
        self._alone: deque[float] = deque(maxlen=20)

    def begin(self, user_id: str | None = None) -> int:
        with self._lock:
            ticket = next(self._tickets)
            self._running[ticket] = (time.monotonic(), not self._running)
            if user_id:
                self._owners[ticket] = user_id
            return ticket

    def running_for(self, user_id: str) -> int:
        """Questions this person has in progress."""
        with self._lock:
            return sum(1 for t in self._running if self._owners.get(t) == user_id)

    def end(self, ticket: int | None = None) -> None:
        with self._lock:
            if ticket is None and self._running:
                ticket = min(self._running)
            started = self._running.pop(ticket, None)
            self._owners.pop(ticket, None)
            self._last_end = time.monotonic()
            if started is not None:
                began, alone = started
                if alone and not self._running:
                    self._alone.append(self._last_end - began)

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._running)

    def idle_for(self, seconds: float) -> bool:
        """No question in progress, and none finished in the last `seconds`."""
        with self._lock:
            return not self._running and time.monotonic() - self._last_end >= seconds

    def snapshot(self) -> frozenset[int]:
        """The questions in progress now — the ones a question arriving now waits behind."""
        with self._lock:
            return frozenset(self._running)

    def still_running(self, tickets: frozenset[int]) -> int:
        with self._lock:
            return sum(1 for t in tickets if t in self._running)

    def typical_seconds(self) -> float:
        """How long one question takes here, from the recent ones that ran alone."""
        with self._lock:
            return statistics.median(self._alone) if self._alone else DEFAULT_ANSWER_SECONDS

    def estimate_wait(self, ahead: int) -> int:
        """Seconds until an answer, for a question with `ahead` others before it."""
        return int(round((ahead + 1) * self.typical_seconds()))


#: The one instance the answer pipeline reports to and background work consults.
ACTIVITY = Activity()
