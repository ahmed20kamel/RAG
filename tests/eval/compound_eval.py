"""Compound questions, answered through the running system and scored part by part.

A compound probe joins two questions from the evaluation set. It passes only when the
gold values of *every* part appear in the answer: covering two parts of three is the
failure being measured, so it scores as a failure however complete the covered parts are.

    python tests/eval/compound_eval.py --label before
    python tests/eval/compound_eval.py --label after
    python tests/eval/compound_eval.py --compare before after
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.text import normalize, strip_thousands  # noqa: E402
from tests import harness_auth  # noqa: E402
from tests.eval.dataset import EQUIVALENTS, QUESTIONS  # noqa: E402
from tests.eval.probes import COMPOUND  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
RESULTS = ROOT / "tests" / "eval" / "results"


def folded(text: str) -> str:
    return strip_thousands(normalize(text))


def present(answer: str, value: str) -> bool:
    haystack = folded(answer)
    return any(folded(v) in haystack for v in (value, *EQUIVALENTS.get(value, ())))


def probes_for(which: str) -> list:
    """Compound probes, or the paraphrase probes as one-part questions."""
    if which == "paraphrase":
        from types import SimpleNamespace

        from tests.eval.probes import PARAPHRASES

        return [SimpleNamespace(qid=p.qid, of=(p.of,), question=p.question) for p in PARAPHRASES]
    return list(COMPOUND)


def run(label: str, which: str = "compound") -> Path:
    by_id = {q.qid: q for q in QUESTIONS}
    rows = []
    with httpx.Client(timeout=900) as client:
        harness_auth.login(client, BASE)
        for probe in probes_for(which):
            started = time.time()
            try:
                reply = client.post(f"{BASE}/api/chat", json={"question": probe.question})
                reply.raise_for_status()
                body = reply.json()
            except httpx.HTTPError as exc:
                print(f"[{probe.qid}] ERROR {type(exc).__name__}")
                rows.append({"qid": probe.qid, "error": type(exc).__name__})
                continue
            answer = body.get("answer", "")
            parts = []
            for qid in probe.of:
                gold = [g for g in by_id[qid].gold if g]
                found = [g for g in gold if present(answer, g)]
                parts.append({"of": qid, "gold": gold, "found": found,
                              "recall": len(found) / len(gold) if gold else 1.0})
            every_part = all(p["found"] for p in parts)
            complete = all(p["recall"] == 1.0 for p in parts)
            rows.append({
                "qid": probe.qid, "question": probe.question,
                "every_part_answered": every_part, "complete": complete,
                "recall": round(statistics.fmean(p["recall"] for p in parts), 3),
                "parts": parts, "latency_s": round(time.time() - started, 1),
                "timings": body.get("timings_ms", {}),
                "plan_parts": (body.get("plan") or {}).get("parts", []),
                "answer": answer,
            })
            mark = "OK" if complete else ("~~" if every_part else "--")
            missing = [f"{p['of']}:{sorted(set(p['gold']) - set(p['found']))}" for p in parts if p["recall"] < 1]
            print(f"[{probe.qid}] {mark} recall={rows[-1]['recall']} {rows[-1]['latency_s']}s "
                  f"{' '.join(missing)}")

    scored = [r for r in rows if "error" not in r]
    summary = {
        "probes": len(rows), "errors": len(rows) - len(scored),
        "every_part_answered": round(statistics.fmean(r["every_part_answered"] for r in scored), 3) if scored else None,
        "complete": round(statistics.fmean(r["complete"] for r in scored), 3) if scored else None,
        "recall": round(statistics.fmean(r["recall"] for r in scored), 3) if scored else None,
        "latency_mean_s": round(statistics.fmean(r["latency_s"] for r in scored), 1) if scored else None,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{which}_{label}.json"
    path.write_text(json.dumps({"label": label, "summary": summary, "rows": rows},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False))
    print(f"saved → {path}")
    return path


def compare(before: str, after: str, which: str = "compound") -> None:
    load = lambda l: json.loads((RESULTS / f"{which}_{l}.json").read_text(encoding="utf-8"))  # noqa: E731
    a, b = load(before), load(after)
    for key in ("every_part_answered", "complete", "recall", "latency_mean_s"):
        print(f"{key:22} {a['summary'].get(key)!s:>8} → {b['summary'].get(key)!s:>8}")
    old = {r["qid"]: r for r in a["rows"]}
    for row in b["rows"]:
        prior = old.get(row["qid"], {})
        if prior.get("recall") != row.get("recall"):
            print(f"  {row['qid']}: recall {prior.get('recall')} → {row.get('recall')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="compound")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--set", default="compound", choices=("compound", "paraphrase"))
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare, which=args.set)
    else:
        run(args.label, args.set)


if __name__ == "__main__":
    main()
