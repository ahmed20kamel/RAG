"""Whether someone is waiting on an answer right now.

There is one graphics card. A question someone is waiting for and a background job —
sorting an upload into its folder — both need the model, and the model serves one at a
time. Background work asks here first and waits for a quiet moment, so a person never
queues behind a filing job.
"""

from __future__ import annotations

import threading
import time


class Activity:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self._last_end = 0.0

    def begin(self) -> None:
        with self._lock:
            self._active += 1

    def end(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            self._last_end = time.monotonic()

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def idle_for(self, seconds: float) -> bool:
        """No question in progress, and none finished in the last `seconds`."""
        with self._lock:
            return self._active == 0 and time.monotonic() - self._last_end >= seconds


#: The one instance the answer pipeline reports to and background work consults.
ACTIVITY = Activity()
