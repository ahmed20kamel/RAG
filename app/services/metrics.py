"""Monitoring: how the system is behaving, recorded for every request.

Without this, the first sign of a slower model, a failing embedder or a corpus change
that made the system refuse more often was a person complaining. Every request now
leaves one row — outcome, per-stage timings, retrieval size, conflicts — and three
views read those rows:

* `summarize()` — the administrator's dashboard: rates, percentiles, a daily series,
  why requests were refused, the slowest answers, and alerts against thresholds.
* `prometheus()` — the same counters in the Prometheus text format, for an existing
  monitoring stack to scrape.
* the health report, which folds the alerts in beside component reachability.

Recording never fails a request. A metrics write that cannot happen is logged and
dropped; the person asking still gets their answer.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.text import normalize
from app.models.database import session_scope
from app.models.metrics import RequestMetric

logger = logging.getLogger(__name__)

#: What each refusal code means to the person reading the dashboard. The codes name the
#: branch that refused; the labels name what happened. "knowledge-only-mode-off" in
#: particular is the production path for "retrieval found nothing", which its name hides.
REASON_LABELS = {
    "knowledge-only-mode-off": "لم يُعثر على أدلة في المستندات",
    "model-read-evidence-and-refused": "وُجدت أدلة ولم يجد النموذج فيها إجابة لهذه الصياغة",
    "no-approved-item-above-threshold": "لا معرفة معتمدة قريبة بما يكفي",
    "unresolved-conflict-between-approved-items": "تعارض غير محسوم بين معرفتين معتمدتين",
    "knowledge-layer-unavailable": "طبقة المعرفة غير متاحة",
    "knowledge-lookup-failed": "تعذّر البحث في المعرفة المعتمدة",
    "insufficient-internal-evidence": "أدلة داخلية غير كافية",
    "ambiguous-document": "اسم الملف في السؤال يطابق أكثر من مستند",
}

#: Upper bounds of the latency histogram, in seconds. Sized for local generation, where
#: an answer takes seconds on a GPU that holds the model and minutes on one that does not.
BUCKETS_S = (1, 2, 5, 10, 20, 30, 60, 90, 120, 180, 300, 600)


def outcome_of(response) -> str:
    """answered | refused | clarified | web, read from the response itself."""
    if response.answer_source == "web":
        return "web"
    if response.grounded:
        return "answered"
    if response.answer_source == "none":
        return "refused"
    # Not grounded yet carrying an answer: the clarification pass offered an
    # interpretation after the model refused the question as worded.
    return "clarified"


def question_hash(question: str) -> str:
    return hashlib.sha256(normalize(question).encode("utf-8")).hexdigest()


@dataclass
class Thresholds:
    p95_ms: int = 240_000
    error_rate: float = 0.05
    refusal_rate: float = 0.40
    min_requests: int = 10


class MetricsRecorder:
    """Records each request, and keeps in-process counters for Prometheus."""

    def __init__(self, reranker_name: str = "", thresholds: Thresholds | None = None) -> None:
        self.reranker_name = reranker_name
        self.thresholds = thresholds or Thresholds()
        self._lock = threading.Lock()
        self._requests: Counter[tuple[str, str]] = Counter()
        self._histogram: dict[str, list[int]] = defaultdict(lambda: [0] * (len(BUCKETS_S) + 1))
        self._sums: Counter[str] = Counter()

    # -- recording ---------------------------------------------------------
    def record(self, channel: str, question: str, response, total_ms: int, user=None) -> None:
        outcome = outcome_of(response)
        timings = dict(response.timings_ms or {})
        validation = response.validation
        row = RequestMetric(
            channel=channel,
            outcome=outcome,
            refusal_reason=(getattr(response, "refusal_reason", "") or "")[:64],
            total_ms=int(timings.get("total_ms") or total_ms),
            retrieval_ms=int(timings.get("retrieval_ms", 0)),
            generation_ms=int(timings.get("generation_ms", 0)),
            stages={k: int(v) for k, v in timings.items() if isinstance(v, (int, float))},
            retrieved=int(response.retrieved_chunks or 0),
            cited=len(validation.cited_sources) if validation else 0,
            conflicts=len(validation.conflicts) if validation else 0,
            unsupported_values=len(validation.unsupported_values) if validation else 0,
            complete=bool(validation.complete) if validation else False,
            completion_pass=bool(getattr(validation, "expanded", False)) if validation else False,
            model=response.model or "",
            reranker=self.reranker_name,
            question_hash=question_hash(question),
            question=question[:1000] if outcome != "answered" else "",
            answer_id=response.answer_id or "",
            user_id=getattr(user, "id", None),
        )
        self._count(channel, outcome, row.total_ms, row.retrieval_ms, row.generation_ms)
        self._persist(row)

    def record_error(self, channel: str, question: str, error: BaseException, total_ms: int,
                     user=None) -> None:
        row = RequestMetric(
            channel=channel, outcome="error", error_type=type(error).__name__[:64],
            total_ms=total_ms, stages={}, model="", reranker=self.reranker_name,
            question_hash=question_hash(question), question=question[:1000],
            user_id=getattr(user, "id", None),
        )
        self._count(channel, "error", total_ms, 0, 0)
        self._persist(row)

    def _count(self, channel: str, outcome: str, total: int, retrieval: int, generation: int) -> None:
        with self._lock:
            self._requests[(channel, outcome)] += 1
            for name, value in (("total", total), ("retrieval", retrieval), ("generation", generation)):
                seconds = value / 1000
                index = next((i for i, b in enumerate(BUCKETS_S) if seconds <= b), len(BUCKETS_S))
                self._histogram[name][index] += 1
                self._sums[name] += seconds

    @staticmethod
    def _persist(row: RequestMetric) -> None:
        try:
            with session_scope() as db:
                db.add(row)
        except Exception:  # noqa: BLE001
            logger.exception("Could not record request metrics; the answer is unaffected")

    # -- Prometheus --------------------------------------------------------
    def prometheus(self, extra: dict[str, float] | None = None) -> str:
        lines = [
            "# HELP rag_requests_total Answered requests by channel and outcome.",
            "# TYPE rag_requests_total counter",
        ]
        with self._lock:
            for (channel, outcome), count in sorted(self._requests.items()):
                lines.append(f'rag_requests_total{{channel="{channel}",outcome="{outcome}"}} {count}')
            for name in ("total", "retrieval", "generation"):
                metric = f"rag_{name}_seconds"
                lines += [f"# HELP {metric} Request {name} time.", f"# TYPE {metric} histogram"]
                cumulative = 0
                counts = self._histogram[name]
                for bound, count in zip(BUCKETS_S, counts):
                    cumulative += count
                    lines.append(f'{metric}_bucket{{le="{bound}"}} {cumulative}')
                cumulative += counts[-1]
                lines.append(f'{metric}_bucket{{le="+Inf"}} {cumulative}')
                lines.append(f"{metric}_sum {self._sums[name]:.3f}")
                lines.append(f"{metric}_count {cumulative}")
        for name, value in (extra or {}).items():
            lines += [f"# TYPE {name} gauge", f"{name} {value}"]
        return "\n".join(lines) + "\n"

    # -- dashboard ---------------------------------------------------------
    def summarize(self, days: int = 7, extra_alerts: list[dict] | None = None) -> dict:
        since = datetime.now(UTC) - timedelta(days=days)
        with session_scope() as db:
            rows = list(db.scalars(
                select(RequestMetric).where(RequestMetric.created_at >= since)
                .order_by(RequestMetric.created_at)
            ))
            summary = _summarize(rows, days, since)
        summary["alerts"] = self._alerts(summary) + (extra_alerts or [])
        return summary

    def _alerts(self, summary: dict) -> list[dict]:
        alerts: list[dict] = []
        total = summary["total"]
        if total < self.thresholds.min_requests:
            return alerts
        rates = summary["rates"]
        p95 = summary["latency"]["total"]["p95"]
        if rates.get("error", 0) > self.thresholds.error_rate:
            alerts.append({"level": "critical", "key": "error_rate",
                           "message": f"نسبة الأخطاء {rates['error']:.0%} تتجاوز "
                                      f"{self.thresholds.error_rate:.0%}"})
        if rates.get("refused", 0) > self.thresholds.refusal_rate:
            alerts.append({"level": "warning", "key": "refusal_rate",
                           "message": f"نسبة الرفض {rates['refused']:.0%} تتجاوز "
                                      f"{self.thresholds.refusal_rate:.0%} — "
                                      "راجع أسباب الرفض أدناه"})
        if p95 > self.thresholds.p95_ms:
            alerts.append({"level": "warning", "key": "latency",
                           "message": f"95% من الإجابات خلال {p95 / 1000:.0f} ث، "
                                      f"والحد {self.thresholds.p95_ms / 1000:.0f} ث"})
        return alerts


def _percentile(values: list[int], share: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return int(ordered[index])


def _spread(values: list[int]) -> dict:
    return {
        "p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95),
        "max": max(values) if values else 0,
        "mean": round(sum(values) / len(values)) if values else 0,
    }


def _summarize(rows: list[RequestMetric], days: int, since: datetime) -> dict:
    total = len(rows)
    outcomes = Counter(r.outcome for r in rows)
    answered = [r for r in rows if r.outcome == "answered"]
    served = [r for r in rows if r.outcome != "error"]

    stages: dict[str, list[int]] = defaultdict(list)
    for row in served:
        for name, value in (row.stages or {}).items():
            if name != "total_ms":
                stages[name].append(int(value))

    daily: dict[str, dict] = {}
    for row in rows:
        day = row.created_at.astimezone().date().isoformat()
        bucket = daily.setdefault(day, {"date": day, "total": 0, "answered": 0, "refused": 0,
                                        "clarified": 0, "error": 0, "web": 0, "_ms": []})
        bucket["total"] += 1
        bucket[row.outcome] = bucket.get(row.outcome, 0) + 1
        if row.outcome != "error":
            bucket["_ms"].append(row.total_ms)
    series = []
    for bucket in daily.values():
        values = bucket.pop("_ms")
        bucket["p50_ms"], bucket["p95_ms"] = _percentile(values, 0.5), _percentile(values, 0.95)
        series.append(bucket)

    slowest = sorted(served, key=lambda r: r.total_ms, reverse=True)[:10]
    errors = [r for r in rows if r.outcome == "error"][-10:]

    return {
        "window_days": days,
        "since": since.isoformat(timespec="seconds"),
        "total": total,
        "by_outcome": dict(outcomes),
        "by_channel": dict(Counter(r.channel for r in rows)),
        "rates": {k: round(v / total, 3) for k, v in outcomes.items()} if total else {},
        "latency": {
            "total": _spread([r.total_ms for r in served]),
            "retrieval": _spread([r.retrieval_ms for r in served if r.retrieval_ms]),
            "generation": _spread([r.generation_ms for r in served if r.generation_ms]),
        },
        "stages": {name: _spread(values) for name, values in sorted(stages.items())},
        "quality": {
            "answered": len(answered),
            "complete_rate": round(sum(r.complete for r in answered) / len(answered), 3) if answered else 0,
            "completion_pass_rate": round(sum(r.completion_pass for r in answered) / len(answered), 3) if answered else 0,
            "with_unsupported_values": sum(1 for r in answered if r.unsupported_values),
            "with_conflicts": sum(1 for r in answered if r.conflicts),
            "mean_retrieved": round(sum(r.retrieved for r in served) / len(served), 1) if served else 0,
        },
        "daily": series,
        "refusal_reasons": [
            {"reason": reason or "", "label": REASON_LABELS.get(reason, reason or "غير محدد"),
             "count": count}
            for reason, count in Counter(r.refusal_reason for r in rows if r.outcome == "refused").most_common(8)
        ],
        "slowest": [
            {"answer_id": r.answer_id, "total_ms": r.total_ms, "outcome": r.outcome,
             "created_at": r.created_at.isoformat(timespec="seconds"), "model": r.model}
            for r in slowest
        ],
        "recent_errors": [
            {"created_at": r.created_at.isoformat(timespec="seconds"), "error_type": r.error_type,
             "channel": r.channel}
            for r in reversed(errors)
        ],
        "models": dict(Counter(r.model for r in served if r.model)),
    }
