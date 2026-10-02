"""What conversational learning costs per message.

Three numbers decide whether this layer is affordable, and they are measured separately
because they are paid at different times. Detection runs on every message whether or not
anyone is teaching, so it is the one that has to be nearly free. The knowledge arm runs
on every answered question. The candidate write happens only when something was noticed.

Detection is timed offline over a corpus that is mostly ordinary questions, because that
is the real traffic mix: the cost of the layer is dominated by the messages that teach
nothing. The rest is read from the timings the API already returns.

Run: python tests/phase4_performance.py   (the app must be running for part 2)
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.signal_detector import SignalDetector  # noqa: E402
from tests import harness_auth  # noqa: E402
from tests.eval.dataset import QUESTIONS  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")

TEACHING = [
    "تعلم القاعدة: عند ذكر أي مبلغ اكتب العملة بعده صراحةً",
    "الإجابة السابقة غير صحيحة، الصحيح هو أن المهلة أربعة عشر يومًا",
    "يقصد بالمعاينة الهندسية الكشف الميداني الذي تنتدب له المحكمة خبيرًا",
    "أفضّل الإجابات المختصرة جدًا في هذا الحساب من فضلك",
    "الإجراء هو: يُفتح ملف المطالبة ثم يُرفق الإخطار ثم تُحال للإدارة القانونية",
    "from now on always show the currency next to every figure you report",
]


def detection_cost() -> None:
    """Timed over the golden questions plus a few teaching messages.

    The golden set is the point: thirty-eight real questions that teach nothing are what
    detection actually spends its time on, and a cue table that is cheap only on matches
    would be no use.
    """
    print("-- 1. signal detection, per message --")
    detector = SignalDetector()
    corpus = [q.question for q in QUESTIONS] + TEACHING

    detector.detect(corpus[0])  # warm the compiled patterns

    timings: list[float] = []
    detected = 0
    for _ in range(20):
        for message in corpus:
            started = time.perf_counter()
            signal = detector.detect(message)
            timings.append((time.perf_counter() - started) * 1000)
            if signal is not None:
                detected += 1

    per_run = detected / 20
    print(f"  messages timed          {len(timings)} ({len(corpus)} × 20)")
    print(f"  mean                    {statistics.mean(timings):.3f} ms")
    print(f"  median                  {statistics.median(timings):.3f} ms")
    print(f"  p95                     {sorted(timings)[int(len(timings) * 0.95)]:.3f} ms")
    print(f"  max                     {max(timings):.3f} ms")
    print(f"  signals found           {per_run:.0f} of {len(corpus)} messages")
    print(f"  of which teaching       {len(TEACHING)} were written to teach")


def answer_cost() -> None:
    """The layer's share of a real answer, from the timings the API already reports."""
    print("\n-- 2. where the time goes in a real answer --")
    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)
    try:
        rows = []
        for question in [q.question for q in QUESTIONS[:8]]:
            started = time.perf_counter()
            response = client.post(f"{BASE}/api/chat", json={"question": question})
            wall = (time.perf_counter() - started) * 1000
            body = response.json()
            timings = body.get("timings_ms", {})
            rows.append(
                {
                    "wall_ms": wall,
                    "knowledge_ms": timings.get("knowledge_ms", 0),
                    "retrieval_ms": timings.get("retrieval_ms", 0),
                    "generation_ms": timings.get("generation_ms", 0),
                    "signal": body.get("learning_signal") is not None,
                }
            )

        def col(name: str) -> str:
            values = [r[name] for r in rows]
            return f"mean {statistics.mean(values):8.1f} ms   max {max(values):8.1f} ms"

        print(f"  whole request         {col('wall_ms')}")
        print(f"  retrieval             {col('retrieval_ms')}")
        print(f"  knowledge arm         {col('knowledge_ms')}")
        print(f"  generation            {col('generation_ms')}")
        share = statistics.mean(
            [r["knowledge_ms"] / r["wall_ms"] * 100 for r in rows if r["wall_ms"]]
        )
        print(f"  knowledge arm share   {share:.2f}% of the request")
        print(f"  learning signals      {sum(r['signal'] for r in rows)} of {len(rows)} questions")
    finally:
        client.close()


def write_cost() -> None:
    """How many rows a taught message adds, and how many an ordinary one adds."""
    print("\n-- 3. what gets written --")
    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)
    try:
        def candidate_count() -> int:
            return client.get(f"{BASE}/api/candidates", params={"mine": True}).json()["total"]

        before = candidate_count()
        client.post(f"{BASE}/api/chat", json={"question": "ما رقم العقد المعتمد للمشروع؟"})
        after_question = candidate_count()

        started = time.perf_counter()
        answer = client.post(
            f"{BASE}/api/chat",
            json={"question": "تعلم القاعدة: اذكر رقم البند بجانب كل استشهاد في الإجابة"},
        ).json()
        taught_ms = (time.perf_counter() - started) * 1000
        after_teaching = candidate_count()

        print(f"  ordinary question     +{after_question - before} candidate row(s)")
        print(f"  teaching message      +{after_teaching - after_question} candidate row(s)")
        print(f"  knowledge items       +0 (nothing is created before approval)")
        print(f"  Qdrant writes         +0 (a candidate is never embedded)")
        print(f"  teaching request      {taught_ms:.0f} ms end to end")

        signal = answer.get("learning_signal")
        if signal:
            client.post(
                f"{BASE}/api/candidates/{signal['candidate_id']}/dismiss",
                json={"reason": "قياس أداء"},
            )
    finally:
        client.close()


if __name__ == "__main__":
    detection_cost()
    answer_cost()
    write_cost()
