"""Run the evaluation set against the live API and write a JSON + text report.

Usage:
    python tests/eval/run_eval.py --label baseline
    python tests/eval/run_eval.py --label after
    python tests/eval/run_eval.py --compare baseline after
"""

from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import os
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests import harness_auth  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from tests.eval.dataset import EQUIVALENTS, QUESTIONS, EvalQuestion  # noqa: E402

# The API port is configurable because another service may already own 8000.
BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
RESULTS_DIR = Path(__file__).resolve().parent / "results"
UPLOADS = ROOT / "data" / "uploads"

ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
DIACRITICS = re.compile(r"[ً-ْـ]")
NUMBER_TOKEN = re.compile(r"\d[\d,./%]*\d|\d")


def normalize(text: str) -> str:
    text = text.translate(ARABIC_INDIC)
    text = DIACRITICS.sub("", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = re.sub(r"[‏‎\*_`]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


ARABIC_MONTHS = (
    "يناير", "فبراير", "مارس", "ابريل", "مايو", "يونيو",
    "يوليو", "اغسطس", "سبتمبر", "اكتوبر", "نوفمبر", "ديسمبر",
)
ENGLISH_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
GOLD_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def date_spellings(token: str) -> list[str]:
    """The same calendar date written the other ways the answer may use.

    "05/03/2026" and "5 مارس 2026" are one date. Only the spelling varies — no new
    value is accepted, so this cannot let an unsupported number through.
    """
    match = GOLD_DATE.match(token.strip())
    if not match:
        return []
    day, month, year = (int(g) for g in match.groups())
    if not 1 <= month <= 12:
        return []
    return [
        f"{day} {ARABIC_MONTHS[month - 1]} {year}",
        f"{day:02d} {ARABIC_MONTHS[month - 1]} {year}",
        f"{day} {ENGLISH_MONTHS[month - 1]} {year}",
        f"{year}-{month:02d}-{day:02d}",
    ]


def _present(haystack: str, needle: str) -> bool:
    hay, need = normalize(haystack), normalize(needle)
    if need and need in hay:
        return True
    # numbers may be written with or without thousands separators
    bare_need, bare_hay = need.replace(",", ""), hay.replace(",", "")
    return bool(bare_need) and bare_need in bare_hay


def contains(haystack: str, needle: str) -> bool:
    """Whether the answer states the gold fact, however it spells it."""
    candidates = [needle, *EQUIVALENTS.get(needle, ()), *date_spellings(needle)]
    return any(_present(haystack, candidate) for candidate in candidates)


def load_corpus() -> str:
    return normalize(
        "\n".join(p.read_text("utf-8", errors="replace") for p in UPLOADS.glob("*.md"))
    ).replace(",", "")


def hallucinated_numbers(answer: str, corpus: str) -> list[str]:
    """Numeric tokens in the answer that exist nowhere in the source documents."""
    invented = []
    for token in NUMBER_TOKEN.findall(normalize(answer)):
        cleaned = token.strip(".,%").replace(",", "")
        if len(cleaned) < 3:  # ignore small numbers like list markers and 1-2 digit values
            continue
        if cleaned not in corpus:
            invented.append(token)
    return sorted(set(invented))


def ask(client: httpx.Client, question: str) -> tuple[dict, float]:
    started = time.perf_counter()
    response = client.post(f"{BASE}/api/chat", json={"question": question}, timeout=1800)
    response.raise_for_status()
    return response.json(), time.perf_counter() - started


def score(item: EvalQuestion, result: dict, elapsed: float, corpus: str) -> dict:
    answer = result.get("answer", "")
    sources = result.get("sources", [])
    grounded = bool(result.get("grounded"))

    row: dict = {
        "qid": item.qid,
        "category": item.category,
        "question": item.question,
        "in_kb": item.in_kb,
        "latency_s": round(elapsed, 2),
        "grounded": grounded,
        "n_sources": len(sources),
        "answer": answer,
        "sources": [
            {"filename": s.get("filename"), "section": s.get("section"), "score": s.get("score")}
            for s in sources
        ],
    }

    if not item.in_kb:
        row["refusal_correct"] = (not grounded) and not sources
        row["completeness"] = 1.0 if row["refusal_correct"] else 0.0
        row["correctness"] = 1.0 if row["refusal_correct"] else 0.0
        row["correctness_basis"] = (
            "refused a question the corpus cannot answer"
            if row["refusal_correct"]
            else "answered a question the corpus cannot support"
        )
        row["hallucinated"] = [] if row["refusal_correct"] else hallucinated_numbers(answer, corpus)
        return row

    found = [g for g in item.gold if contains(answer, g)]
    missing = [g for g in item.gold if g not in found]
    row["completeness"] = round(len(found) / len(item.gold), 3) if item.gold else 0.0
    row["gold_found"] = found
    row["gold_missing"] = missing

    source_text = " ".join(f"{s.get('filename', '')} {s.get('section', '')}" for s in sources)
    row["document_hit"] = (item.document is None) or any(
        s.get("filename") == item.document for s in sources
    )
    row["citation_hit"] = (not item.sections) or any(
        contains(source_text, fragment) for fragment in item.sections
    )
    row["hallucinated"] = hallucinated_numbers(answer, corpus)
    row["answered"] = grounded

    # Completeness and correctness are different questions, and reporting one number for
    # both is how "100% complete" came to sit beside "incomplete" on the same screen.
    #
    #   completeness — how much of what the answer had to contain is in it.
    #   correctness  — whether what it does contain is supported by the right evidence.
    #
    # They are independent. An answer can name every required figure and rest on the
    # wrong document; it can rest on exactly the right passage and state only half of
    # what was asked. Averaging them, or letting one stand in for the other, hides
    # precisely the failure each is there to catch.
    faults: list[str] = []
    if row["hallucinated"]:
        faults.append(f"states values absent from the corpus: {', '.join(row['hallucinated'][:3])}")
    if not row["document_hit"]:
        faults.append("does not rest on the expected document")
    if not row["citation_hit"]:
        faults.append("does not cite the expected section")
    if not grounded:
        faults.append("not grounded in any retrieved evidence")
    row["correctness"] = 0.0 if faults else 1.0
    row["correctness_basis"] = "; ".join(faults) or "supported by the expected evidence"
    return row


def aggregate(rows: list[dict]) -> dict:
    in_kb = [r for r in rows if r["in_kb"]]
    out_kb = [r for r in rows if not r["in_kb"]]
    latencies = [r["latency_s"] for r in rows]

    summary = {
        "questions": len(rows),
        # How much of what each answer had to contain is in it.
        "answer_completeness": round(statistics.mean(r["completeness"] for r in in_kb), 3),
        "fully_complete_rate": round(
            sum(1 for r in in_kb if r["completeness"] == 1.0) / len(in_kb), 3
        ),
        # Whether what each answer contains is supported by the right evidence. Reported
        # beside completeness rather than folded into it: the two fail in different ways
        # and a single number would let one hide the other.
        "answer_correctness": round(
            statistics.mean(r.get("correctness", 0.0) for r in in_kb), 3
        ),
        "answered_rate": round(sum(1 for r in in_kb if r.get("answered")) / len(in_kb), 3),
        "document_accuracy": round(sum(1 for r in in_kb if r["document_hit"]) / len(in_kb), 3),
        "citation_accuracy": round(sum(1 for r in in_kb if r["citation_hit"]) / len(in_kb), 3),
        "hallucination_rate": round(sum(1 for r in rows if r["hallucinated"]) / len(rows), 3),
        "refusal_accuracy": round(
            sum(1 for r in out_kb if r["refusal_correct"]) / len(out_kb), 3
        ) if out_kb else None,
        "latency_mean_s": round(statistics.mean(latencies), 1),
        "latency_median_s": round(statistics.median(latencies), 1),
        "latency_max_s": round(max(latencies), 1),
    }

    by_category: dict[str, dict] = {}
    for row in rows:
        bucket = by_category.setdefault(
            row["category"], {"n": 0, "completeness": 0.0, "correctness": 0.0}
        )
        bucket["n"] += 1
        bucket["completeness"] += row["completeness"]
        bucket["correctness"] += row.get("correctness", 0.0)
    for name, bucket in by_category.items():
        bucket["completeness"] = round(bucket["completeness"] / bucket["n"], 3)
        bucket["correctness"] = round(bucket["correctness"] / bucket["n"], 3)
    summary["by_category"] = by_category
    return summary


def run(label: str, only: str | None, qids: list[str] | None = None) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus()
    items = [
        q for q in QUESTIONS
        if (only is None or q.category == only) and (qids is None or q.qid in qids)
    ]
    print(f"Running {len(items)} questions  (label={label})\n")

    rows: list[dict] = []
    with httpx.Client(timeout=1800) as client:
        harness_auth.login(client, BASE)

        for index, item in enumerate(items, start=1):
            try:
                result, elapsed = ask(client, item.question)
            except Exception as exc:  # noqa: BLE001 - one bad question must not kill the run
                print(f"[{index:>2}/{len(items)}] {item.qid} ERROR: {exc}")
                rows.append({
                    "qid": item.qid, "category": item.category, "question": item.question,
                    "in_kb": item.in_kb, "latency_s": 0.0, "grounded": False, "n_sources": 0,
                    "answer": f"ERROR: {exc}", "sources": [], "completeness": 0.0,
                    "hallucinated": [], "document_hit": False, "citation_hit": False,
                    "refusal_correct": False, "answered": False,
                })
                continue

            row = score(item, result, elapsed, corpus)
            rows.append(row)
            marker = "OK " if row["completeness"] == 1.0 else "PART" if row["completeness"] > 0 else "MISS"
            extra = ""
            if item.in_kb and row.get("gold_missing"):
                extra = f"  missing={row['gold_missing']}"
            correct = "ok" if row.get("correctness", 0.0) == 1.0 else "BAD"
            print(f"[{index:>2}/{len(items)}] {item.qid:<3} {marker} "
                  f"comp={row['completeness']:.2f} corr={correct:<3} "
                  f"src={row['n_sources']} {row['latency_s']:>6.1f}s{extra}")
            if row.get("correctness", 1.0) < 1.0:
                print(f"           └ {row.get('correctness_basis', '')}")

    summary = aggregate(rows)
    payload = {
        "label": label,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": httpx.get(f"{BASE}/api/config", timeout=30).json(),
        "summary": summary,
        "rows": rows,
    }
    out = RESULTS_DIR / f"{label}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print(f"SUMMARY ({label})")
    print("=" * 62)
    for key, value in summary.items():
        if key != "by_category":
            print(f"  {key:<24} {value}")
    print("  by_category:")
    for name, bucket in summary["by_category"].items():
        print(
            f"    {name:<16} n={bucket['n']:<3} "
            f"completeness={bucket['completeness']:<6} "
            f"correctness={bucket.get('correctness', '—')}"
        )
    print(
        "\n  completeness = how much of what the answer had to contain is in it."
        "\n  correctness  = whether what it contains rests on the right evidence and"
        "\n                 states nothing the corpus does not support."
        "\n  They are independent: an answer can be complete and wrong, or correct and"
        "\n  partial, and one number for both hides whichever failed."
    )
    print(f"\nsaved → {out}")


def compare(before: str, after: str) -> None:
    a = json.loads((RESULTS_DIR / f"{before}.json").read_text("utf-8"))
    b = json.loads((RESULTS_DIR / f"{after}.json").read_text("utf-8"))
    print(f"{'metric':<26}{before:>14}{after:>14}{'delta':>12}")
    print("-" * 66)
    for key, value in a["summary"].items():
        if key == "by_category" or value is None:
            continue
        other = b["summary"].get(key)
        if other is None:
            continue
        delta = round(other - value, 3)
        print(f"{key:<26}{value:>14}{other:>14}{delta:>+12}")

    print(f"\n{'category':<18}{before:>12}{after:>12}{'delta':>10}")
    print("-" * 52)
    for name, bucket in a["summary"]["by_category"].items():
        other = b["summary"]["by_category"].get(name, {}).get("completeness")
        if other is None:
            continue
        print(f"{name:<18}{bucket['completeness']:>12}{other:>12}{round(other - bucket['completeness'], 3):>+10}")

    print("\nper-question completeness change:")
    rows_a = {r["qid"]: r for r in a["rows"]}
    for row in b["rows"]:
        old = rows_a.get(row["qid"])
        if old is None or old["completeness"] == row["completeness"]:
            continue
        arrow = "improved" if row["completeness"] > old["completeness"] else "REGRESSED"
        print(f"  {row['qid']:<4} {old['completeness']:.2f} → {row['completeness']:.2f}  {arrow}"
              f"   {row['question'][:52]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run")
    parser.add_argument("--only", default=None, help="restrict to one category")
    parser.add_argument("--qids", default=None, help="comma-separated question ids to run")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
    else:
        run(args.label, args.only, args.qids.split(",") if args.qids else None)
