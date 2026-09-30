"""Retrieval measured on its own — no generation, minutes instead of an hour.

The golden run answers every question with the language model, which on this hardware
takes one to two minutes a question. That is the right final measurement and the wrong
tool for tuning: a ranking weight explored at an hour per setting is a weight nobody
explores. Almost every retrieval change can be judged before generation, because an
answer cannot contain what the evidence handed to the model does not.

Reads the same question set as `run_eval.py` and never modifies it. For each question
that the corpus can answer, it runs the production analyser and retriever in-process
and records:

    doc_hit        the expected document is among the retrieved passages
    section_hit    a passage from an expected section is retrieved
    section_mrr    1 / rank of the first such passage — how high it came
    gold_recall    the share of required values present in the retrieved text; an
                   answer can reach at most this completeness
    latency_ms     retrieval and reranking time

Questions the corpus cannot answer are reported separately, as the number of passages
retrieved for them: reranking should not make the system more confident about nothing.

    python tests/eval/retrieval_eval.py --label feature
    python tests/eval/retrieval_eval.py --label cross --reranker cross --weight 2
    python tests/eval/retrieval_eval.py --compare feature cross
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.text import normalize, strip_thousands  # noqa: E402
from tests.eval.dataset import QUESTIONS, EvalQuestion  # noqa: E402
from tests.eval import probes as _probes  # noqa: E402

ORDER_PROBES = _probes.ORDER_PROBES
PARAPHRASES = _probes.PARAPHRASES
#: Optional in a probe file written before compound probes existed.
COMPOUND = getattr(_probes, "COMPOUND", [])

RESULTS = ROOT / "tests" / "eval" / "results"
METRICS = ("doc_hit", "section_hit", "section_mrr", "gold_recall")


def _folded(text: str) -> str:
    return strip_thousands(normalize(text))


def build_container(args: argparse.Namespace):
    from app.config import get_settings
    from app.container import Container

    overrides = {}
    if args.reranker:
        overrides["reranker"] = args.reranker
    if args.weight is not None:
        overrides["reranker_weight"] = args.weight
    if args.top_n is not None:
        overrides["reranker_top_n"] = args.top_n
    if args.model_dir:
        overrides["reranker_model_dir"] = args.model_dir
    if args.max_tokens is not None:
        overrides["reranker_max_tokens"] = args.max_tokens
    settings = get_settings().model_copy(update=overrides)
    return Container(settings), settings


def measure(container, question) -> dict:
    analysis = container.query_analyzer.analyze(question.question)
    started = time.perf_counter()
    candidates = container.retriever.retrieve(analysis)
    latency = (time.perf_counter() - started) * 1000

    sections = [_folded(c.section) for c in candidates]
    wanted = [_folded(s) for s in question.sections]
    rank = next(
        (i + 1 for i, s in enumerate(sections) if any(w and w in s for w in wanted)), None
    )
    text = _folded(" ".join(f"{c.section} {c.content}" for c in candidates))
    gold = [g for g in question.gold if g]
    found = [g for g in gold if _folded(g) in text]

    return {
        "qid": question.qid,
        "category": question.category,
        "in_kb": question.in_kb,
        "retrieved": len(candidates),
        "doc_hit": float(
            question.document is None
            or any(c.filename == question.document for c in candidates)
        ),
        "section_hit": float(rank is not None) if wanted else None,
        "section_mrr": (1.0 / rank if rank else 0.0) if wanted else None,
        "gold_recall": len(found) / len(gold) if gold else None,
        "missing_gold": [g for g in gold if g not in found],
        "latency_ms": round(latency),
        "top": [c.section.split("→")[-1].strip()[:50] for c in candidates[:3]],
    }


def measure_order(container, probe) -> dict:
    """Whether the passage stating the value in force comes before the superseded one."""
    analysis = container.query_analyzer.analyze(probe.question)
    candidates = container.retriever.retrieve(analysis)

    def first(values: tuple[str, ...]) -> int | None:
        needles = [_folded(v) for v in values]
        for rank, c in enumerate(candidates, 1):
            text = _folded(f"{c.section} {c.content}")
            if any(n in text for n in needles):
                return rank
        return None

    wanted, superseded = first(probe.wanted), first(probe.superseded)
    in_order = wanted is not None and (superseded is None or wanted < superseded)
    return {"qid": probe.qid, "wanted_rank": wanted, "superseded_rank": superseded,
            "in_order": float(in_order), "retrieved": len(candidates)}


def measure_compound(container, probe) -> dict:
    """Per-part evidence for a compound question: the whole search alone, and with parts.

    Both in one run, so the comparison is between the two strategies on identical state
    rather than between two runs.
    """
    from types import SimpleNamespace

    by_id = {q.qid: q for q in QUESTIONS}
    analysis = container.query_analyzer.analyze(probe.question)
    whole = container.retriever.retrieve(analysis)
    service = container.rag_service
    with_parts = (
        service._with_part_evidence(analysis, list(whole), SimpleNamespace(category=None, document_ids=None))
        if service._is_compound(analysis) else list(whole)
    )

    def score(candidates) -> tuple[bool, float]:
        text = _folded(" ".join(f"{c.section} {c.content}" for c in candidates))
        recalls = []
        for qid in probe.of:
            gold = [g for g in by_id[qid].gold if g]
            recalls.append(sum(_folded(g) in text for g in gold) / len(gold) if gold else 1.0)
        return all(r > 0 for r in recalls), statistics.fmean(recalls)

    whole_hit, whole_recall = score(whole)
    parts_hit, parts_recall = score(with_parts)
    return {
        "qid": probe.qid, "compound": service._is_compound(analysis),
        "parts": analysis.parts_display,
        "whole_every_part": float(whole_hit), "whole_recall": round(whole_recall, 3),
        "parts_every_part": float(parts_hit), "parts_recall": round(parts_recall, 3),
        "added": len(with_parts) - len(whole),
    }


def paraphrase_questions() -> list[EvalQuestion]:
    by_id = {q.qid: q for q in QUESTIONS}
    return [
        EvalQuestion(p.qid, by_id[p.of].category, p.question, by_id[p.of].gold,
                     by_id[p.of].document, by_id[p.of].sections)
        for p in PARAPHRASES
    ]


def summarise(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["in_kb"]]
    out: dict = {"questions": len(answerable)}
    for metric in METRICS:
        values = [r[metric] for r in answerable if r[metric] is not None]
        out[metric] = round(statistics.fmean(values), 3) if values else None
    latencies = [r["latency_ms"] for r in rows]
    out["latency_mean_ms"] = round(statistics.fmean(latencies)) if latencies else 0
    out["latency_p95_ms"] = (
        round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]) if latencies else 0
    )
    refused = [r for r in rows if not r["in_kb"]]
    out["out_of_kb"] = len(refused)
    out["out_of_kb_retrieved_mean"] = (
        round(statistics.fmean(r["retrieved"] for r in refused), 1) if refused else 0
    )
    per_category: dict[str, dict] = defaultdict(dict)
    for category in sorted({r["category"] for r in answerable}):
        members = [r for r in answerable if r["category"] == category]
        for metric in METRICS:
            values = [r[metric] for r in members if r[metric] is not None]
            per_category[category][metric] = round(statistics.fmean(values), 3) if values else None
    out["per_category"] = dict(per_category)
    return out


def run(args: argparse.Namespace) -> Path:
    container, settings = build_container(args)
    reranker = container.reranker
    print(f"retrieval eval — reranker={reranker.name}"
          + (f" weight={settings.reranker_weight} top_n={settings.reranker_top_n}"
             f" model={settings.reranker_model_dir}" if reranker.name == "cross" else ""))
    container.keyword_index.ensure_loaded()

    rows = []
    for question in QUESTIONS:
        row = measure(container, question)
        rows.append(row)
        mark = "·" if not row["in_kb"] else (
            "OK" if (row["gold_recall"] in (None, 1.0) and row["section_hit"] in (None, 1.0))
            else "--"
        )
        print(f"[{row['qid']:>3}] {mark:2} gold={row['gold_recall']} sec@={row['section_mrr']} "
              f"n={row['retrieved']:2} {row['latency_ms']:5}ms  {' | '.join(row['top'])[:90]}")

    summary = summarise(rows)

    print("\n-- paraphrases: golden questions reworded --")
    para_rows = []
    for question in paraphrase_questions():
        row = measure(container, question)
        para_rows.append(row)
        mark = "OK" if row["gold_recall"] == 1.0 and row["section_hit"] == 1.0 else "--"
        print(f"[{row['qid']:>5}] {mark} gold={row['gold_recall']} sec@={row['section_mrr']} "
              f"{' | '.join(row['top'])[:80]}")
    para = summarise(para_rows)
    summary["paraphrase"] = {k: para[k] for k in (*METRICS, "questions")}

    print("\n-- supersession order: the value in force before the one it replaced --")
    order_rows = []
    for probe in ORDER_PROBES:
        row = measure_order(container, probe)
        order_rows.append(row)
        print(f"[{row['qid']:>3}] {'OK' if row['in_order'] else '--'} "
              f"in-force@{row['wanted_rank']} superseded@{row['superseded_rank']}")
    summary["order"] = {
        "probes": len(order_rows),
        "in_order": (
            round(statistics.fmean(r["in_order"] for r in order_rows), 3) if order_rows else None
        ),
    }
    if COMPOUND:
        print("\n-- compound: evidence for every part, whole search vs part by part --")
        compound_rows = []
        for probe in COMPOUND:
            row = measure_compound(container, probe)
            compound_rows.append(row)
            print(f"[{row['qid']:>4}] parts={len(row['parts'])} "
                  f"whole: every={row['whole_every_part']:.0f} recall={row['whole_recall']} | "
                  f"parts: every={row['parts_every_part']:.0f} recall={row['parts_recall']} (+{row['added']})")
        summary["compound"] = {
            "probes": len(compound_rows),
            "detected": round(statistics.fmean(r["compound"] for r in compound_rows), 3),
            "whole_every_part": round(statistics.fmean(r["whole_every_part"] for r in compound_rows), 3),
            "parts_every_part": round(statistics.fmean(r["parts_every_part"] for r in compound_rows), 3),
            "whole_recall": round(statistics.fmean(r["whole_recall"] for r in compound_rows), 3),
            "parts_recall": round(statistics.fmean(r["parts_recall"] for r in compound_rows), 3),
        }
        rows += [dict(r, category="compound", in_kb=True) for r in compound_rows]
    rows = rows + para_rows + [dict(r, category="order", in_kb=True) for r in order_rows]
    summary["reranker"] = reranker.name
    if reranker.name == "cross":
        summary["reranker_settings"] = {
            "weight": settings.reranker_weight, "top_n": settings.reranker_top_n,
            "model_dir": settings.reranker_model_dir,
            "max_tokens": settings.reranker_max_tokens,
            "status": reranker.scorer.status.as_dict(),
        }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"retrieval_{args.label}.json"
    path.write_text(json.dumps({"label": args.label, "summary": summary, "rows": rows},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps({k: v for k, v in summary.items() if k != "per_category"},
                            ensure_ascii=False))
    print(f"saved → {path}")
    return path


def compare(before: str, after: str) -> None:
    load = lambda label: json.loads((RESULTS / f"retrieval_{label}.json").read_text(encoding="utf-8"))  # noqa: E731
    a, b = load(before), load(after)
    print(f"{'metric':26} {before:>12} {after:>12}   Δ")
    def pick(summary: dict, metric: str):
        if "." in metric:
            group, key = metric.split(".", 1)
            return (summary.get(group) or {}).get(key)
        return summary.get(metric)

    for metric in (*METRICS, *(f"paraphrase.{m}" for m in METRICS), "order.in_order",
                   "latency_mean_ms", "latency_p95_ms", "out_of_kb_retrieved_mean"):
        x, y = pick(a["summary"], metric), pick(b["summary"], metric)
        delta = (y - x) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else ""
        print(f"{metric:26} {x!s:>12} {y!s:>12}   {delta:+.3f}" if delta != "" else
              f"{metric:26} {x!s:>12} {y!s:>12}")
    rows_a = {r["qid"]: r for r in a["rows"]}
    changed = []
    for r in b["rows"]:
        old = rows_a.get(r["qid"])
        if not old or not r.get("in_kb"):
            continue
        for metric in ("gold_recall", "section_mrr", "in_order"):
            if metric not in r:
                continue
            if old.get(metric) != r[metric]:
                changed.append(f"  {r['qid']:>3} {metric}: {old[metric]} → {r[metric]}")
    print("\nper question:" if changed else "\nno per-question differences")
    print("\n".join(changed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="retrieval")
    parser.add_argument("--reranker", choices=("feature", "cross"))
    parser.add_argument("--weight", type=float)
    parser.add_argument("--top-n", type=int, dest="top_n")
    parser.add_argument("--max-tokens", type=int, dest="max_tokens")
    parser.add_argument("--model-dir", dest="model_dir")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
    else:
        run(args)


if __name__ == "__main__":
    main()
