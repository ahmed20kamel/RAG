"""A question asked at a busy moment is told where it stands.

The model answers one question at a time, so the questions already being answered when a
new one arrives are the ones it waits behind. This checks the count a newcomer is given,
that it falls as those finish, and that the expected wait is built from answers that ran
alone — one that queued would count its wait as its own time and inflate every estimate.

Offline, no server. Run: python tests/test_load_status.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.services.activity import DEFAULT_ANSWER_SECONDS, Activity  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def main() -> int:
    a = Activity()
    check(a.estimate_wait(0) == int(DEFAULT_ANSWER_SECONDS), "untimed machine: one question's default wait")

    print("\n=== the queue a newcomer sees ===")
    first = a.begin()
    second = a.begin()
    arriving = a.snapshot()
    check(len(arriving) == 2, "two questions in progress: the newcomer is told two are ahead")
    third = a.begin()
    check(a.still_running(arriving) == 2, "its own question is not counted as ahead of it")
    a.end(first)
    check(a.still_running(arriving) == 1, "one finishes: one ahead")
    a.end(second)
    check(a.still_running(arriving) == 0, "both finish: nothing ahead")
    a.end(third)
    check(a.active == 0, "everything finished: nothing active")

    print("\n=== the estimate ===")
    alone = a.begin()
    time.sleep(0.05)
    a.end(alone)
    typical = a.typical_seconds()
    check(0.04 <= typical < 1, "an answer that ran alone is what a question is timed by", f"{typical:.3f}s")
    queued_first = a.begin()
    queued_second = a.begin()
    time.sleep(0.2)
    a.end(queued_first)
    a.end(queued_second)
    check(a.typical_seconds() == typical, "answers that overlapped do not change the estimate",
          f"{a.typical_seconds():.3f}s vs {typical:.3f}s")
    check(a.estimate_wait(3) == round(4 * typical), "three ahead: four answers' time")

    print("\n=== background work still waits for quiet ===")
    check(not a.idle_for(10), "just finished: not idle yet")
    busy = a.begin()
    check(not a.idle_for(0), "a question in progress: not idle")
    a.end(busy)
    check(a.idle_for(0), "nothing in progress: idle")

    print("\n=== the model's queue ===")
    import threading

    from app.services.model_gate import AbandonedError, GatedLLM, ModelGate, watch

    order: list[str] = []
    release_first = threading.Event()

    class Slow:
        model = "m"

        def chat(self, system_prompt, user_prompt):
            order.append(user_prompt)
            if user_prompt == "first":
                release_first.wait(5)
            return user_prompt

    gate = ModelGate()
    llm = GatedLLM(Slow(), gate)
    check(llm.model == "m", "everything but chat reaches the client unchanged")
    results: dict[str, object] = {}

    def ask(label, flag=None):
        watch(flag)
        try:
            results[label] = llm.chat("s", label)
        except AbandonedError:
            results[label] = "withdrawn"

    gone = threading.Event()
    threads = [threading.Thread(target=ask, args=("first",))]
    threads[0].start()
    time.sleep(0.2)
    for label, flag in (("left", gone), ("second", None)):
        threads.append(threading.Thread(target=ask, args=(label, flag)))
        threads[-1].start()
        time.sleep(0.1)
    check(gate.waiting == 2, "two wait behind the one being answered", str(gate.waiting))
    gone.set()
    time.sleep(1.5)
    check(results.get("left") == "withdrawn", "a reader who left is withdrawn before reaching the model")
    release_first.set()
    for t in threads:
        t.join(5)
    check(order == ["first", "second"], "the model sees the rest one at a time, in order", str(order))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
