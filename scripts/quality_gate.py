"""The quality gate: one command that says whether the system still behaves.

Before, the checks existed and nothing ran them. The golden set was run by hand, a
single run was read as a measurement, and a regression could pass because nobody looked.
This runs them in order, compares against an accepted baseline, and exits non-zero on a
regression — so a scheduler, a deployment script or a person gets the same answer.

Stages, cheapest first. A later stage runs only if the earlier ones pass.

  1. offline   every self-contained suite in tests/. Seconds; needs nothing running.
  2. core      the RAG-core fingerprint. A FROZEN file that changed fails the gate unless
               named with --allow-frozen, which is how a deliberate change is declared.
  3. retrieval retrieval and ranking against the question set, without generation.
               Minutes; needs Qdrant and the embedding model, not the language model.
  4. answers   (--full N) the full question set answered N times through a running
               server. The mean is compared, and the range reported: one run is not a
               measurement, because generation varies between identical runs.

    python scripts/quality_gate.py                    # stages 1–3
    python scripts/quality_gate.py --full 3           # and 4, three runs
    python scripts/quality_gate.py --accept           # make this run the baseline

The baseline is `tests/eval/baselines/gate.json`. Accepting is explicit and recorded,
never automatic: a gate that moves its own bar measures nothing.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

PYTHON = sys.executable
TESTS = ROOT / "tests"
EVAL = TESTS / "eval"
RESULTS = EVAL / "results"
BASELINE = EVAL / "baselines" / "gate.json"

#: Suites that need a running server, a model, or files outside the repository.
#: `RAG_BASE_URL`, not `BASE_URL`: the shorter one also matched `DATABASE_URL`, which
#: every suite with its own temporary database sets, and kept six offline suites out
#: of the gate.
LIVE_MARKERS = ("harness_auth", "httpx.Client", "RAG_BASE_URL", "PDF_CORPUS_DIR")

#: How far each retrieval metric may fall below the baseline before the gate fails.
RETRIEVAL_TOLERANCE = {
    "doc_hit": 0.0, "section_hit": 0.0, "gold_recall": 0.01, "section_mrr": 0.03,
    "paraphrase.doc_hit": 0.0, "paraphrase.section_hit": 0.0,
    "paraphrase.gold_recall": 0.01, "paraphrase.section_mrr": 0.03,
}
#: For answers: a mean may fall this far; the hard limits may not move at all.
ANSWER_TOLERANCE = {
    "answer_completeness": 0.02, "answer_correctness": 0.02, "fully_complete_rate": 0.03,
    "document_accuracy": 0.03, "citation_accuracy": 0.03,
}
ANSWER_HARD = {"refusal_accuracy": ("min", 1.0)}
#: May not rise above the worst run of the accepted baseline. An absolute zero would fail
#: a baseline that is itself not zero, and a gate that always fails is not read.
ANSWER_NO_WORSE = ("hallucination_rate",)


def run(command: list[str], timeout: int, env: dict | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, env={**os.environ, **(env or {})},
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def pick(summary: dict, metric: str):
    if "." in metric:
        group, key = metric.split(".", 1)
        return (summary.get(group) or {}).get(key)
    return summary.get(metric)


# ---------------------------------------------------------------------------


def stage_offline() -> dict:
    suites = sorted(
        path for path in TESTS.glob("test_*.py")
        if not any(marker in path.read_text(encoding="utf-8") for marker in LIVE_MARKERS)
    )
    results = []
    for suite in suites:
        started = time.perf_counter()
        try:
            code, output = run([PYTHON, str(suite)], timeout=900)
        except subprocess.TimeoutExpired:
            code, output = 1, "timed out after 900s"
        failed = [line for line in output.splitlines() if line.startswith("[FAIL]")]
        results.append({
            "suite": suite.name, "ok": code == 0 and not failed,
            "failed_checks": failed[:10], "seconds": round(time.perf_counter() - started, 1),
            "tail": "" if code == 0 else output.strip().splitlines()[-1:],
        })
        print(f"  {'PASS' if results[-1]['ok'] else 'FAIL'}  {suite.name}"
              + (f"  ({len(failed)} check(s))" if failed else ""))
    return {"ok": all(r["ok"] for r in results), "suites": results}


def stage_core(baseline: dict, allow_frozen: set[str]) -> dict:
    if not (TESTS / "rag_core_checksums.py").exists():
        print("  no core fingerprint tool in tests/ — stage skipped")
        return {"ok": True, "skipped": "no fingerprint tool"}
    label = baseline.get("checksum_label")
    if not label or not (RESULTS / f"checksums_{label}.json").exists():
        print("  no accepted checksum baseline — recorded as a finding, not a failure")
        return {"ok": True, "skipped": "no baseline"}
    _code, output = run([PYTHON, "tests/rag_core_checksums.py", "--compare", label], timeout=120)
    lines = [line.strip() for line in output.splitlines()]
    # "  ! path" marks a changed FROZEN file, "  ~ path" a changed DECLARED one.
    frozen = [line[2:].strip() for line in lines if line.startswith("! ")]
    declared = [line[2:].strip() for line in lines if line.startswith("~ ")]
    violations = [f for f in frozen if f not in allow_frozen]
    print(f"  declared files changed: {len(declared)}")
    print(f"  frozen files changed without --allow-frozen: {violations or 'none'}")
    return {"ok": not violations, "violations": violations, "allowed": sorted(allow_frozen),
            "declared_changed": declared, "frozen_changed": frozen}


def stage_retrieval(baseline: dict) -> dict:
    if not (EVAL / "dataset.py").exists():
        print("  no evaluation set in tests/eval — write one for this corpus to enable this stage")
        return {"ok": True, "skipped": "no dataset"}
    label = f"gate_{datetime.now():%Y%m%d_%H%M%S}"
    try:
        code, output = run([PYTHON, "tests/eval/retrieval_eval.py", "--label", label], timeout=3600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "retrieval evaluation timed out"}
    path = RESULTS / f"retrieval_{label}.json"
    if code != 0 or not path.exists():
        return {"ok": False, "error": "retrieval evaluation failed",
                "tail": output.strip().splitlines()[-5:]}
    summary = json.loads(path.read_text(encoding="utf-8"))["summary"]
    reference = baseline.get("retrieval") or {}
    regressions = []
    for metric, tolerance in RETRIEVAL_TOLERANCE.items():
        now, before = pick(summary, metric), pick(reference, metric)
        if isinstance(now, (int, float)) and isinstance(before, (int, float)) and now < before - tolerance:
            regressions.append(f"{metric}: {before} → {now}")
    for metric in RETRIEVAL_TOLERANCE:
        print(f"  {metric:26} {pick(reference, metric)!s:>8} → {pick(summary, metric)!s:>8}")
    return {"ok": not regressions, "regressions": regressions, "summary": summary,
            "label": label, "baseline_present": bool(reference)}


def stage_answers(baseline: dict, runs: int, base_url: str) -> dict:
    if not (EVAL / "run_eval.py").exists():
        return {"ok": True, "skipped": "no evaluation harness"}
    summaries = []
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    for index in range(1, runs + 1):
        label = f"gate_{stamp}_run{index}"
        print(f"  run {index}/{runs} …")
        try:
            code, output = run([PYTHON, "tests/eval/run_eval.py", "--label", label],
                               timeout=4 * 3600, env={"RAG_BASE_URL": base_url})
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"run {index} timed out"}
        path = RESULTS / f"{label}.json"
        if code != 0 or not path.exists():
            return {"ok": False, "error": f"run {index} failed", "tail": output.strip().splitlines()[-5:]}
        summaries.append(json.loads(path.read_text(encoding="utf-8"))["summary"])

    metrics = sorted({*ANSWER_TOLERANCE, *ANSWER_HARD, *ANSWER_NO_WORSE})
    aggregate = {}
    for metric in metrics:
        values = [s[metric] for s in summaries if isinstance(s.get(metric), (int, float))]
        if values:
            aggregate[metric] = {"mean": round(statistics.fmean(values), 3),
                                 "min": min(values), "max": max(values)}
    reference = baseline.get("answers") or {}
    regressions = []
    for metric, tolerance in ANSWER_TOLERANCE.items():
        now = (aggregate.get(metric) or {}).get("mean")
        before = (reference.get(metric) or {}).get("mean")
        if now is not None and before is not None and now < before - tolerance:
            regressions.append(f"{metric}: mean {before} → {now}")
    for metric, (kind, limit) in ANSWER_HARD.items():
        worst = (aggregate.get(metric) or {}).get("min" if kind == "min" else "max")
        if worst is not None and ((kind == "min" and worst < limit) or (kind == "max" and worst > limit)):
            regressions.append(f"{metric}: worst run {worst}, limit {limit}")
    for metric in ANSWER_NO_WORSE:
        worst = (aggregate.get(metric) or {}).get("max")
        ceiling = (reference.get(metric) or {}).get("max")
        if worst is not None and ceiling is not None and worst > ceiling:
            regressions.append(f"{metric}: worst run {worst}, baseline worst {ceiling}")
    for metric in metrics:
        a, b = reference.get(metric) or {}, aggregate.get(metric) or {}
        print(f"  {metric:22} {a.get('mean')!s:>7} → {b.get('mean')!s:>7}  "
              f"range {b.get('min')}–{b.get('max')}")
    return {"ok": not regressions, "regressions": regressions, "aggregate": aggregate,
            "runs": runs}


# ---------------------------------------------------------------------------


def write_report(report: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    path = RESULTS / f"gate_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Quality gate — {report['started']}", "",
             f"**Result: {'PASS' if report['ok'] else 'FAIL'}**", ""]
    for name, stage in report["stages"].items():
        state = "skipped" if stage.get("skipped") else ("pass" if stage["ok"] else "FAIL")
        lines.append(f"- **{name}**: {state}")
        for item in stage.get("regressions", []) + stage.get("violations", []):
            lines.append(f"  - {item}")
        for suite in stage.get("suites", []):
            if not suite["ok"]:
                lines.append(f"  - {suite['suite']}: {len(suite['failed_checks'])} failed check(s)")
    (RESULTS / f"gate_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS / "gate_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    return path


def accept(report: dict, reason: str) -> None:
    """Make this run the baseline — explicitly, with a reason, and with history kept."""
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    label = f"gate_baseline_{datetime.now():%Y%m%d_%H%M%S}"
    run([PYTHON, "tests/rag_core_checksums.py", "--save", label], timeout=120)
    baseline = {
        "accepted": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "checksum_label": label,
        "retrieval": (report["stages"].get("retrieval") or {}).get("summary")
        or previous.get("retrieval"),
        "answers": (report["stages"].get("answers") or {}).get("aggregate")
        or previous.get("answers"),
        "history": (previous.get("history") or []) + (
            [{k: previous.get(k) for k in ("accepted", "reason", "checksum_label")}]
            if previous else []
        ),
    }
    BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"baseline accepted → {BASELINE}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", type=int, default=0, metavar="N",
                        help="also answer the question set N times through a running server")
    parser.add_argument("--base-url", default=os.environ.get("RAG_BASE_URL", "http://localhost:8080"))
    parser.add_argument("--allow-frozen", default="",
                        help="comma-separated FROZEN files whose change is deliberate")
    parser.add_argument("--skip-retrieval", action="store_true")
    parser.add_argument("--accept", action="store_true", help="make this run the new baseline")
    parser.add_argument("--reason", default="", help="why the baseline moved (with --accept)")
    args = parser.parse_args()

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    report: dict = {"started": datetime.now().isoformat(timespec="seconds"), "stages": {},
                    "baseline_accepted": baseline.get("accepted")}
    allow = {f.strip() for f in args.allow_frozen.split(",") if f.strip()}

    plan = [("offline", stage_offline), ("core", lambda: stage_core(baseline, allow))]
    if not args.skip_retrieval:
        plan.append(("retrieval", lambda: stage_retrieval(baseline)))
    if args.full:
        plan.append(("answers", lambda: stage_answers(baseline, args.full, args.base_url)))

    for name, stage in plan:
        print(f"\n== {name} ==")
        started = time.perf_counter()
        result = stage()
        result["seconds"] = round(time.perf_counter() - started, 1)
        report["stages"][name] = result
        if not result["ok"]:
            print(f"  → {name} FAILED; later stages not run")
            break

    report["ok"] = all(s["ok"] for s in report["stages"].values())
    path = write_report(report)
    print(f"\n{'PASS' if report['ok'] else 'FAIL'} — report: {path}")

    if args.accept:
        if not report["ok"] and baseline:
            print("not accepting a failing run as the baseline")
            return 1
        if not args.reason:
            print("--accept needs --reason: a baseline that moves has to say why")
            return 1
        accept(report, args.reason)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
