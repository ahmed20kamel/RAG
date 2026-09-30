"""Measuring what changes when approved knowledge may answer alone.

Production refuses a question the documents do not cover. The experimental mode lets
approved knowledge try instead. That is a real shift in where the system draws the line
between answering and declining, so it is measured rather than argued about.

The probe set is built for this: questions the corpus provably does not cover, half of
them backed by a taught claim and half by nothing at all. The first half tests whether
knowledge can answer; the second tests whether the mode still refuses when it should.

Run with the app started in each mode:
    KNOWLEDGE_ANSWER_MODE=gated   python tests/knowledge_only_experiment.py --label gated
    KNOWLEDGE_ANSWER_MODE=eligible python tests/knowledge_only_experiment.py --label eligible
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
RESULTS = ROOT / "tests" / "eval" / "results"
INSUFFICIENT = "لا توجد معلومات كافية"

MARK = uuid.uuid4().hex[:6]

#: Claims taught for the experiment. Each is about an invented subject, so nothing in the
#: gold corpus can answer it and the only possible source is the taught item itself.
TAUGHT = [
    {
        "content": f"سياسة المناوبة الليلية في مشروع أوريون {MARK} تتطلب مشرفًا معتمدًا وفريقًا من ثلاثة أفراد",
        "source_text": "قرار التشغيل رقم 12",
        "question": f"ما سياسة المناوبة الليلية في مشروع أوريون {MARK}؟",
        "must_contain": "ثلاثة",
    },
    {
        "content": f"مدة صلاحية تصريح الدخول لمشروع أوريون {MARK} تسعون يومًا من تاريخ الإصدار",
        "source_text": "دليل الأمن الداخلي",
        "question": f"كم مدة صلاحية تصريح الدخول في مشروع أوريون {MARK}؟",
        "must_contain": "تسعون",
    },
    {
        "content": f"جهة الاعتماد لطلبات التمديد في مشروع أوريون {MARK} هي لجنة العمليات وليس إدارة المشاريع",
        "source_text": "محضر اللجنة",
        "question": f"من يعتمد طلبات التمديد في مشروع أوريون {MARK}؟",
        "must_contain": "لجنة العمليات",
    },
]

#: Questions with no source of any kind. A correct system refuses every one of these in
#: both modes; the experimental mode must not turn them into answers.
UNSUPPORTED = [
    f"ما ميزانية التسويق لمشروع أوريون {MARK}؟",
    f"كم عدد المركبات المخصصة لمشروع أوريون {MARK}؟",
    f"ما اسم مدير الجودة في مشروع أوريون {MARK}؟",
    "ما هو راتب مهندس الموقع في الشركة؟",
]


def refused(answer: dict) -> bool:
    return not answer.get("grounded", False) or INSUFFICIENT in answer.get("answer", "")


def run(label: str) -> None:
    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)
    taught_ids: list[str] = []
    rows: list[dict] = []

    try:
        for entry in TAUGHT:
            made = client.post(
                f"{BASE}/api/knowledge",
                json={
                    "type": "fact",
                    "scope": "global",
                    "content": entry["content"],
                    "source_text": entry["source_text"],
                },
            )
            made.raise_for_status()
            item_id = made.json()["id"]
            taught_ids.append(item_id)
            client.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "تجربة"})
            client.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})
        # The vector write is asynchronous, and three seconds was not always enough:
        # a probe that ran too early scored the item at zero and the mode refused for
        # a reason that had nothing to do with the mode. Ten seconds removed it.
        time.sleep(10)

        print(f"\nRunning the knowledge-only probe ({label})\n")
        for entry in TAUGHT:
            started = time.perf_counter()
            answer = client.post(f"{BASE}/api/chat", json={"question": entry["question"]}).json()
            elapsed = time.perf_counter() - started
            knowledge = answer.get("knowledge", [])
            rows.append(
                {
                    "kind": "supported",
                    "question": entry["question"],
                    "answered": not refused(answer),
                    "correct": entry["must_contain"] in answer.get("answer", ""),
                    "cited_source": entry["source_text"] in answer.get("answer", ""),
                    "knowledge_items": len(knowledge),
                    "document_sources": len(answer.get("sources", [])),
                    "latency_s": round(elapsed, 2),
                    "answer": answer.get("answer", "")[:220],
                }
            )
            state = "answered" if rows[-1]["answered"] else "refused"
            print(f"  supported   {state:9} correct={rows[-1]['correct']}  {elapsed:5.1f}s")

        for question in UNSUPPORTED:
            started = time.perf_counter()
            answer = client.post(f"{BASE}/api/chat", json={"question": question}).json()
            elapsed = time.perf_counter() - started
            rows.append(
                {
                    "kind": "unsupported",
                    "question": question,
                    "answered": not refused(answer),
                    "correct": refused(answer),  # refusing IS the correct outcome here
                    "cited_source": False,
                    "knowledge_items": len(answer.get("knowledge", [])),
                    "document_sources": len(answer.get("sources", [])),
                    "latency_s": round(elapsed, 2),
                    "answer": answer.get("answer", "")[:220],
                }
            )
            state = "answered" if rows[-1]["answered"] else "refused"
            print(f"  unsupported {state:9} (refusal is correct)  {elapsed:5.1f}s")
    finally:
        for item_id in taught_ids:
            client.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "تنظيف"})
        client.close()

    supported = [r for r in rows if r["kind"] == "supported"]
    unsupported = [r for r in rows if r["kind"] == "unsupported"]
    latencies = [r["latency_s"] for r in rows]

    summary = {
        "label": label,
        "mode": os.environ.get("KNOWLEDGE_ANSWER_MODE", "(from .env)"),
        "supported_answered_rate": round(sum(r["answered"] for r in supported) / len(supported), 3),
        "supported_correct_rate": round(sum(r["correct"] for r in supported) / len(supported), 3),
        "knowledge_citation_rate": round(
            sum(r["cited_source"] for r in supported) / len(supported), 3
        ),
        "unsupported_refusal_rate": round(
            sum(not r["answered"] for r in unsupported) / len(unsupported), 3
        ),
        # Anything asserted for an unsupported question came from nowhere.
        "hallucination_rate": round(sum(r["answered"] for r in unsupported) / len(unsupported), 3),
        "latency_mean_s": round(statistics.mean(latencies), 2),
        "latency_max_s": round(max(latencies), 2),
    }

    print("\n" + "=" * 62)
    print(f"KNOWLEDGE-ONLY EXPERIMENT ({label})")
    print("=" * 62)
    for key, value in summary.items():
        print(f"  {key:28} {value}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"knowledge_only_{label}.json"
    out.write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nsaved → {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    run(parser.parse_args().label)
