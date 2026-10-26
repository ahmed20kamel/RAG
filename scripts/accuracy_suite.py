"""The accuracy suite: hundreds of questions from the company's own files, each with the
facts a right answer must contain, asked of the system and checked — every night, and
after every change.

    python scripts/accuracy_suite.py run              # answer every question (resumes)
    python scripts/accuracy_suite.py run --only X01   # some questions again
    python scripts/accuracy_suite.py run --failed     # the ones that failed last time
    python scripts/accuracy_suite.py report           # score the latest run

``data/eval/suite.json`` holds the questions (case material: never committed). Each is

    {"id": "MJD-CON-03", "document": "CONTRACT.pdf", "kind": "fact",
     "question": "...", "expected": "the right answer, in words",
     "facts": [["1,250", "1250"], ["90,000"]],      # every group: any one of them
     "quote": "the passage of the document that says so"}

``kind`` is fact | table | date | party | version | cross | analysis | absent. An
``absent`` question asks for something no document says: right is a refusal, wrong is
any figure that was not asked for.

Polite to the people using the system: before each question it waits while anyone is
waiting on an answer from the server, so a run never stands between a person and theirs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

SUITE = ROOT / "data" / "eval" / "suite.json"
RUNS = ROOT / "data" / "eval" / "runs"
REPORT = ROOT / "data" / "reports" / "accuracy-latest.md"

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_REFUSAL = re.compile(
    r"لا توجد معلومات|لم أجد|لم أتأكد|غير متوفر|غير مذكور|غير محدد|لا تذكر|لا يذكر|لم تذكر|لم يذكر|لم يُذكر|لم تُذكر|"
    r"لا يحدد|لا تحدد|لم يحدد|لم تحدد|لم يرد|لا يرد|لم يحكم|لم يُحكم|لا يوجد في المصادر|لا يوجد في الملفات|فارغة|فارغتان|"
    r"not enough information|could not find|not stated|not specified|"
    r"not mentioned|does not state|do not state",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    text = text.translate(_DIGITS)
    text = re.sub(r"[ًٌٍَُِّْـ]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", text).lower()


_DATE = re.compile(r"(\d{1,4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,4})")


def _dates(text: str) -> set[tuple[int, int, int]]:
    """Every date in the text as (year, month, day), however it is written:
    2026/08/13, 13-08-2026 and «2026 / 08 / 13» are one date."""
    found = set()
    for a, b, c in _DATE.findall(text.translate(_DIGITS)):
        if len(a) == 4:
            year, month, day = int(a), int(b), int(c)
        elif len(c) == 4:
            year, month, day = int(c), int(b), int(a)
        else:
            continue
        if 1 <= month <= 12 and 1 <= day <= 31:
            found.add((year, month, day))
    return found


def _has(answer: str, fact: str) -> bool:
    """A fact is in the answer: as written, as the same figure without separators, or as
    the same date in another format."""
    plain = _norm(answer)
    if _norm(fact) in plain:
        return True
    wanted = _dates(fact)
    if wanted and wanted <= _dates(answer):
        return True
    figure = fact.replace(",", "")
    if re.fullmatch(r"[\d.]+", figure):
        return figure in plain.replace(",", "")
    return False


def judge(item: dict, answer: str, cited: list[str]) -> dict:
    if item.get("kind") == "absent":
        refused = bool(_REFUSAL.search(answer))
        return {"ok": refused, "missing": [] if refused else ["(should have said it is not in the files)"],
                "cited_document": None}
    missing = [group[0] for group in item.get("facts", []) if not any(_has(answer, fact) for fact in group)]
    document = item.get("document")
    cited_document = None if not document else any(document in source for source in cited)
    return {"ok": not missing, "missing": missing, "cited_document": cited_document}


def _wait_while_people_wait(base_url: str) -> None:
    import httpx

    for _ in range(120):
        try:
            if not httpx.get(f"{base_url}/api/system/busy", timeout=5).json().get("busy"):
                return
        except Exception:  # noqa: BLE001 - no server here: nobody to wait for
            return
        time.sleep(5)


def run(only: set[str] | None, failed: bool, base_url: str) -> Path:
    from app.config import get_settings
    from app.container import Container
    from app.schemas.chat import ChatRequest

    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    if failed:
        last = latest_run()
        previous = {r["id"]: r for r in _read(last)} if last else {}
        only = {i for i, r in previous.items() if not r["ok"]}
    items = [q for q in suite if not only or q["id"] in only]

    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"run-{datetime.now():%Y%m%d-%H%M}.jsonl"
    done = {r["id"] for r in _read(path)} if path.exists() else set()
    service = Container(get_settings()).rag_service
    print(f"{len(items)} question(s) -> {path.name}", flush=True)
    for item in items:
        if item["id"] in done:
            continue
        _wait_while_people_wait(base_url)
        started = time.perf_counter()
        try:
            response = service.answer(ChatRequest(question=item["question"], fresh=True), user=None)
            answer, cited = response.answer, sorted({s.filename for s in response.sources})
        except Exception as exc:  # noqa: BLE001 - one failure is a result, not the end of the run
            answer, cited = f"ERROR: {type(exc).__name__}: {exc}", []
        verdict = judge(item, answer, cited)
        record = {"id": item["id"], "kind": item.get("kind"), "document": item.get("document"),
                  "seconds": round(time.perf_counter() - started), "answer": answer, "sources": cited, **verdict}
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        mark = "PASS" if verdict["ok"] else "FAIL"
        print(f"{mark} {item['id']} ({record['seconds']}s)" + (f" missing {verdict['missing']}" if not verdict["ok"] else ""),
              flush=True)
    return path


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_run() -> Path | None:
    runs = sorted(RUNS.glob("run-*.jsonl")) if RUNS.exists() else []
    return runs[-1] if runs else None


def report(path: Path | None = None) -> str:
    path = path or latest_run()
    if path is None:
        return "No run yet."
    suite = {q["id"]: q for q in json.loads(SUITE.read_text(encoding="utf-8"))}
    results = _read(path)
    passed = sum(r["ok"] for r in results)
    lines = [f"# Accuracy — {path.stem}", "",
             f"**{passed} of {len(results)} correct ({100 * passed / max(1, len(results)):.1f}%)**", ""]
    by_kind: dict[str, list[int]] = {}
    for r in results:
        tally = by_kind.setdefault(r.get("kind") or "?", [0, 0])
        tally[0] += r["ok"]
        tally[1] += 1
    lines += ["| kind | correct |", "|---|---|"] + [f"| {k} | {a}/{b} |" for k, (a, b) in sorted(by_kind.items())]
    wrong_source = [r for r in results if r["ok"] and r.get("cited_document") is False]
    lines += ["", f"Right facts but the expected document not cited: {len(wrong_source)}", "", "## Failures", ""]
    for r in results:
        if r["ok"]:
            continue
        q = suite.get(r["id"], {})
        lines += [f"### {r['id']} — {q.get('document', '')}", f"- Q: {q.get('question', '')}",
                  f"- expected: {q.get('expected', '')}", f"- missing: {r['missing']}",
                  f"- answer: {r['answer'][:600]}", ""]
    text = "\n".join(lines)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--only", nargs="*")
    go.add_argument("--failed", action="store_true")
    go.add_argument("--server", default="http://127.0.0.1:8080")
    sub.add_parser("report")
    args = parser.parse_args()
    if not SUITE.exists():
        # A machine without the case files has no questions about them: nothing to do.
        print(f"No suite at {SUITE}; nothing to run.")
        return 0
    if args.command == "run":
        path = run(set(args.only) if args.only else None, args.failed, args.server)
        print(report(path).split("## Failures")[0])
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
