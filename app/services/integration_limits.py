"""What stops one machine caller from taking the whole system.

The thing being rationed is not this endpoint — it is the single Ollama instance behind
it, which also answers every person using the web interface. A generation takes tens of
seconds and runs one at a time. Five concurrent calls from a program are not five times
faster; they are five requests queued behind each other, all of them eventually timing
out, and every human on the same model waiting behind them.

So the limits here are shaped by that measurement rather than by a round number: a
concurrency ceiling low enough that people keep a share of the model, a rate that
matches what the model can actually produce, and a daily quota that stops a caller stuck
in a loop from running all night.

**Process-local.** The buckets and gates live in this process. That is correct for the
current deployment — one uvicorn process — and would have to move to shared storage
before a second worker is added, or each worker would enforce the full limit on its own.
The daily quota is the exception: it is counted from the database, so it holds however
many processes there are.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.exceptions import IdempotencyConflictError, RateLimitedError
from app.models.integration import IntegrationIdempotency, IntegrationRequest

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def body_fingerprint(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# Rate
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    tokens: float
    capacity: float
    refill_per_second: float
    updated: float


class RateLimiter:
    """A token bucket per credential.

    A bucket rather than a fixed window because a fixed window lets a caller spend its
    whole minute in the first second, which on a queue this shallow is the same as no
    limit at all.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, per_minute: int, burst: int) -> None:
        capacity = float(max(burst, 1))
        rate = max(per_minute, 1) / 60.0
        now = time.monotonic()

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(capacity, capacity, rate, now)
                self._buckets[key] = bucket
            else:
                bucket.capacity = capacity
                bucket.refill_per_second = rate
                bucket.tokens = min(capacity, bucket.tokens + (now - bucket.updated) * rate)
                bucket.updated = now

            if bucket.tokens < 1.0:
                wait = max(1, int((1.0 - bucket.tokens) / rate) + 1)
                raise RateLimitedError(
                    f"تجاوزت حد المعدل المسموح ({per_minute} طلبًا في الدقيقة).",
                    error_code="RATE_LIMITED",
                    headers={"Retry-After": str(wait)},
                )
            bucket.tokens -= 1.0


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@dataclass
class _Gate:
    semaphore: asyncio.Semaphore
    limit: int
    waiting: int = 0


class ConcurrencyGate:
    """Caps how many answers one credential can have in flight, and how many may queue.

    Refusing at the door with a 429 is kinder than accepting a request that will sit for
    two minutes and then time out: the caller learns immediately that it is asking for
    more than the system has, and can back off instead of holding a socket open.
    """

    def __init__(self) -> None:
        self._gates: dict[str, _Gate] = {}
        self._lock = threading.Lock()

    def _gate(self, key: str, limit: int) -> _Gate:
        with self._lock:
            gate = self._gates.get(key)
            if gate is None or gate.limit != limit:
                gate = _Gate(asyncio.Semaphore(max(limit, 1)), max(limit, 1))
                self._gates[key] = gate
            return gate

    async def acquire(self, key: str, *, limit: int, queue_depth: int) -> _Gate:
        gate = self._gate(key, limit)
        if gate.semaphore.locked() and gate.waiting >= max(queue_depth, 0):
            raise RateLimitedError(
                "عدد الطلبات المتزامنة تجاوز المسموح لهذا المفتاح.",
                error_code="CONCURRENCY_LIMITED",
                headers={"Retry-After": "10"},
            )
        gate.waiting += 1
        try:
            await gate.semaphore.acquire()
        finally:
            gate.waiting -= 1
        return gate

    @staticmethod
    def release(gate: _Gate) -> None:
        gate.semaphore.release()


# ---------------------------------------------------------------------------
# Daily quota
# ---------------------------------------------------------------------------


def quota_used_today(db: DbSession, client_id: str) -> int:
    """Answers actually produced for this credential since midnight UTC.

    Counts successful replies only. A caller with a malformed request is already stopped
    by the rate limiter, and spending its day's allowance on its own bug would turn one
    mistake into an outage lasting until midnight.
    """
    midnight = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        db.scalar(
            select(func.count())
            .select_from(IntegrationRequest)
            .where(
                IntegrationRequest.client_id == client_id,
                IntegrationRequest.created_at >= midnight,
                IntegrationRequest.status_code == 200,
            )
        )
        or 0
    )


def check_quota(db: DbSession, client_id: str, daily_quota: int) -> None:
    if daily_quota <= 0:
        return
    used = quota_used_today(db, client_id)
    if used >= daily_quota:
        tomorrow = (_utcnow() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait = max(1, int((tomorrow - _utcnow()).total_seconds()))
        raise RateLimitedError(
            f"استُهلكت الحصة اليومية ({daily_quota} طلبًا).",
            error_code="QUOTA_EXCEEDED",
            headers={"Retry-After": str(wait)},
        )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@dataclass
class IdempotentReplay:
    """A stored reply, returned instead of answering the same question twice."""

    status_code: int
    payload: dict = field(default_factory=dict)


class IdempotencyStore:
    """Makes a retry cost nothing and change nothing.

    A retry after a timeout is the expected case here, not the exceptional one:
    generation takes tens of seconds and networks drop. Without this, every retry buys a
    second full generation and a second `answer_traces` row — and one question answered
    twice in two wordings makes the audit trail read like a contradiction that never
    happened.
    """

    def __init__(self, ttl_hours: int = 24) -> None:
        self.ttl = timedelta(hours=ttl_hours)

    def begin(
        self, db: DbSession, *, client_id: str, key: str, body_hash: str
    ) -> IdempotentReplay | None:
        """Claims the key, or hands back what the first call already produced."""
        self._expire(db, client_id)

        row = db.scalar(
            select(IntegrationIdempotency).where(
                IntegrationIdempotency.client_id == client_id,
                IntegrationIdempotency.idempotency_key == key,
            )
        )
        if row is not None:
            return self._replay(row, body_hash)

        db.add(
            IntegrationIdempotency(
                client_id=client_id,
                idempotency_key=key,
                body_hash=body_hash,
                state="in_progress",
            )
        )
        try:
            db.flush()
        except IntegrityError:
            # Two copies of the same retry arriving together. The loser reads the row
            # the winner just wrote rather than starting a second generation.
            db.rollback()
            row = db.scalar(
                select(IntegrationIdempotency).where(
                    IntegrationIdempotency.client_id == client_id,
                    IntegrationIdempotency.idempotency_key == key,
                )
            )
            if row is None:
                raise
            return self._replay(row, body_hash)
        return None

    @staticmethod
    def _replay(row: IntegrationIdempotency, body_hash: str) -> IdempotentReplay:
        if row.body_hash != body_hash:
            raise IdempotencyConflictError(
                "نفس مفتاح التكرار استُخدم مع طلب مختلف.",
                error_code="IDEMPOTENCY_CONFLICT",
            )
        if row.state != "done":
            raise IdempotencyConflictError(
                "هناك طلب بنفس مفتاح التكرار قيد التنفيذ.",
                error_code="IDEMPOTENCY_IN_PROGRESS",
            )
        return IdempotentReplay(
            status_code=row.status_code or 200,
            payload=json.loads(row.response_json or "{}"),
        )

    def complete(
        self,
        db: DbSession,
        *,
        client_id: str,
        key: str,
        status_code: int,
        payload: dict,
        answer_id: str = "",
    ) -> None:
        row = db.scalar(
            select(IntegrationIdempotency).where(
                IntegrationIdempotency.client_id == client_id,
                IntegrationIdempotency.idempotency_key == key,
            )
        )
        if row is None:
            return
        row.state = "done"
        row.status_code = status_code
        row.response_json = json.dumps(payload, ensure_ascii=False)
        row.answer_id = answer_id
        row.completed_at = _utcnow()

    def abandon(self, db: DbSession, *, client_id: str, key: str) -> None:
        """Drops an unfinished claim so a failed call can genuinely be retried.

        Without this, a request that died on the way to an answer would leave its key
        claimed, and every retry would be told a request is still in progress until the
        row expired hours later.
        """
        db.execute(
            delete(IntegrationIdempotency).where(
                IntegrationIdempotency.client_id == client_id,
                IntegrationIdempotency.idempotency_key == key,
                IntegrationIdempotency.state != "done",
            )
        )

    def _expire(self, db: DbSession, client_id: str) -> None:
        db.execute(
            delete(IntegrationIdempotency).where(
                IntegrationIdempotency.client_id == client_id,
                IntegrationIdempotency.created_at < _utcnow() - self.ttl,
            )
        )
