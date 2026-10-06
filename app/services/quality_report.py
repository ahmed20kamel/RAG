"""How good the answers are, and how much the system has learned — over time, measured.

Everything here is counted from what actually happened, not estimated:

* **answer rate** — questions answered from the documents, of all questions asked;
* **verified** — answers in which every figure was found in their sources;
* **satisfaction** — thumbs up of all thumbs given;
* **learned** — corrections, terms and rewordings the system took on, and follow-ups it
  read in context;
* the questions it still could not answer, and the answers people marked wrong — the
  list of what to fix next.

Each figure is shown for the whole period and for the last seven days against the seven
before them, so a change is visible as a change and not as a feeling.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.knowledge import KnowledgeScope, KnowledgeStatus, KnowledgeType
from app.models.database import session_scope
from app.models.knowledge_items import KnowledgeItem
from app.models.metrics import RequestMetric
from app.models.question_log import QuestionRecord
from app.services.query_rewrite import canonicalize


def _day(value: datetime) -> date:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone().date()


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def quality_report(days: int = 30) -> dict:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    with session_scope() as db:
        metrics = list(db.scalars(select(RequestMetric).where(RequestMetric.created_at >= since)))
        log = list(db.scalars(select(QuestionRecord).where(QuestionRecord.created_at >= since)))
        learned = list(db.scalars(select(KnowledgeItem).where(KnowledgeItem.created_at >= since)))
        active_personal = len(list(db.scalars(select(KnowledgeItem.id).where(
            KnowledgeItem.scope == KnowledgeScope.USER, KnowledgeItem.status == KnowledgeStatus.ACTIVE,
        ))))
        db.expunge_all()

    # -- per day ---------------------------------------------------------------
    by_day: dict[date, dict] = defaultdict(lambda: Counter())
    for m in metrics:
        bucket = by_day[_day(m.created_at)]
        bucket["total"] += 1
        bucket[m.outcome] += 1
        if m.outcome == "answered":
            bucket["verified"] += int(not m.unsupported_values)
    for r in log:
        if r.feedback in ("up", "down"):
            by_day[_day(r.created_at)][r.feedback] += 1
    for k in learned:
        if str(k.scope) == "user":
            by_day[_day(k.created_at)]["learned"] += 1
    for r in log:
        if r.resolved_by:
            by_day[_day(r.created_at)]["learned"] += 1

    first = _day(since)
    series = []
    for offset in range(days + 1):
        day = first + timedelta(days=offset)
        c = by_day.get(day, Counter())
        series.append({
            "date": day.isoformat(),
            "total": c["total"],
            "answered": c["answered"],
            "refused": c["refused"],
            "clarified": c["clarified"],
            "errors": c["error"],
            "verified": c["verified"],
            "up": c["up"],
            "down": c["down"],
            "learned": c["learned"],
            "answer_rate": _rate(c["answered"], c["total"]),
            "verified_rate": _rate(c["verified"], c["answered"]),
        })

    # -- the period, and the last week against the one before --------------------
    def window(start: datetime, end: datetime) -> dict:
        ms = [m for m in metrics if start <= (m.created_at if m.created_at.tzinfo else m.created_at.replace(tzinfo=UTC)) < end]
        rs = [r for r in log if start <= (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)) < end]
        answered = [m for m in ms if m.outcome == "answered"]
        up = sum(1 for r in rs if r.feedback == "up")
        down = sum(1 for r in rs if r.feedback == "down")
        times = [m.total_ms / 1000 for m in answered if m.total_ms and not (m.stages or {}).get("remembered")]
        return {
            "questions": len(ms),
            "answer_rate": _rate(len(answered), len(ms)),
            "verified_rate": _rate(sum(1 for m in answered if not m.unsupported_values), len(answered)),
            "satisfaction": _rate(up, up + down),
            "up": up,
            "down": down,
            "remembered": sum(1 for m in ms if (m.stages or {}).get("remembered")),
            "median_seconds": round(statistics.median(times), 1) if times else None,
            "users": len({m.user_id for m in ms if m.user_id}),
        }

    week = now - timedelta(days=7)
    totals = window(since, now + timedelta(seconds=1))
    this_week = window(week, now + timedelta(seconds=1))
    last_week = window(week - timedelta(days=7), week)

    # -- what it learned ----------------------------------------------------------
    personal = [k for k in learned if str(k.scope) == "user"]
    learned_summary = {
        "corrections": sum(1 for k in personal if str(k.type) == KnowledgeType.CORRECTION),
        "terms": sum(1 for k in personal if str(k.type) == KnowledgeType.TERMINOLOGY),
        "from_rephrasing": sum(1 for k in personal if "learned-from-rephrasing" in (k.tags or [])),
        "other": sum(1 for k in personal if str(k.type) not in (KnowledgeType.CORRECTION, KnowledgeType.TERMINOLOGY)),
        "wordings": sum(1 for r in log if r.resolved_by),
        "follow_ups": sum(1 for r in log if r.subject),
        "shared_pending": sum(1 for k in learned if str(k.scope) != "user" and str(k.status) in ("pending", "in_review")),
        "active_personal": active_personal,
    }

    # -- what to fix next ---------------------------------------------------------
    missed: Counter = Counter()
    wording: dict[str, str] = {}
    for m in metrics:
        if m.outcome != "answered" and (m.question or "").strip():
            key = canonicalize(m.question).text
            missed[key] += 1
            wording.setdefault(key, m.question.strip())
    unanswered = [{"question": wording[k][:200], "count": n} for k, n in missed.most_common(10)]
    disliked = [
        {"question": r.question[:200], "answer": " ".join((r.answer or "").split())[:220],
         "date": r.created_at.isoformat()}
        for r in sorted((r for r in log if r.feedback == "down"), key=lambda r: r.created_at, reverse=True)[:10]
    ]

    return {
        "days": days,
        "series": series,
        "totals": totals,
        "this_week": this_week,
        "last_week": last_week,
        "learned": learned_summary,
        "unanswered": unanswered,
        "disliked": disliked,
    }
