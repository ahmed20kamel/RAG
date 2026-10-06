"""The quality page's figures are counted right.

Answer rate, verified figures, satisfaction and learning are computed from the request
metrics, the question log and the knowledge items. This builds a known week and a known
week before it and checks every figure, including that a remembered answer's near-zero
time is not counted as the system being fast.

Offline: a temporary database. Run: python tests/test_quality_report.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-quality-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"

from app.models.database import engine, init_database, session_scope  # noqa: E402
from app.models.metrics import RequestMetric  # noqa: E402
from app.models.question_log import QuestionRecord  # noqa: E402
from app.services.quality_report import quality_report  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def metric(when: datetime, outcome: str, unsupported: int = 0, ms: int = 30000, remembered: bool = False,
           question: str = "") -> RequestMetric:
    return RequestMetric(created_at=when, channel="chat", outcome=outcome, unsupported_values=unsupported,
                         total_ms=ms, stages={"remembered": 1} if remembered else {}, question=question,
                         user_id=None)


def main() -> int:
    init_database()
    now = datetime.now(UTC)
    this_week = now - timedelta(days=2)
    last_week = now - timedelta(days=10)
    with session_scope() as db:
        # This week: 4 questions, 3 answered (2 verified), 1 refused twice-asked; a remembered one.
        db.add_all([
            metric(this_week, "answered", 0, 40000),
            metric(this_week, "answered", 1, 60000),
            metric(this_week, "answered", 0, 50, remembered=True),
            metric(this_week, "refused", question="ما رقم الرخصة؟"),
        ])
        # Last week: 2 questions, both answered and verified.
        db.add_all([metric(last_week, "answered"), metric(last_week, "answered")])
        db.add_all([
            QuestionRecord(user_id=None, question="q1", canonical="q1", outcome="answered", answer="a",
                           response={}, document_ids=[], feedback="up", created_at=this_week),
            QuestionRecord(user_id=None, question="q2", canonical="q2", outcome="answered", answer="a",
                           response={}, document_ids=[], feedback="up", created_at=this_week),
            QuestionRecord(user_id=None, question="q3", canonical="q3", outcome="answered", answer="b",
                           response={}, document_ids=[], feedback="down", created_at=this_week),
            QuestionRecord(user_id=None, question="وكم مدته؟", canonical="x", outcome="answered", answer="c",
                           response={}, document_ids=[], subject="ما قيمة العقد؟", created_at=this_week),
        ])

    report = quality_report(30)
    week, before = report["this_week"], report["last_week"]
    check(week["questions"] == 4 and week["answer_rate"] == 0.75, "this week: 3 of 4 answered", str(week))
    check(week["verified_rate"] == round(2 / 3, 3), "this week: 2 of 3 answers fully verified", str(week["verified_rate"]))
    check(week["satisfaction"] == round(2 / 3, 3) and week["up"] == 2 and week["down"] == 1,
          "satisfaction is thumbs up of all thumbs", str(week))
    check(week["median_seconds"] == 50.0, "a remembered answer does not count as fast", str(week["median_seconds"]))
    check(week["remembered"] == 1, "remembered answers are counted on their own")
    check(before["answer_rate"] == 1.0 and before["verified_rate"] == 1.0, "last week is measured apart", str(before))
    check(report["learned"]["follow_ups"] == 1, "a follow-up read in context counts as learned use")
    check(report["unanswered"] and report["unanswered"][0]["question"] == "ما رقم الرخصة؟",
          "unanswered questions are listed to fix")
    check(len(report["disliked"]) == 1 and report["disliked"][0]["question"] == "q3", "thumbs-down answers are listed")
    check(len(report["series"]) == 31, "one point per day of the period")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        engine.dispose()
        shutil.rmtree(WORKDIR, ignore_errors=True)
    sys.exit(code)
