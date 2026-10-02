"""The measured failures, re-run against the live system — and their unseen siblings.

The unit suites prove the new signals behave correctly on fixtures the author wrote.
That is necessary and not sufficient: a fixture proves the rule, not that the rule
reaches the real index through the real pipeline. This runs the whole thing.

What it does *not* do is assert that one particular section of one particular file comes
back. The expectation is derived from the corpus at run time — the newest dated section
of whichever document the index actually holds — so the suite keeps its meaning after
documents are added, replaced or removed, and cannot be satisfied by special-casing a
question. Point it at a different corpus and it still asks the right thing.

Run:  python tests/test_retrieval_regression_live.py   (needs the app running)
"""

from __future__ import annotations

import io
import os
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.text import all_dates, normalize  # noqa: E402
from app.services.evidence_class import classify_section  # noqa: E402
from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
DB = ROOT / "data" / "rag.db"
FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def ask(client: httpx.Client, question: str) -> dict:
    """One question, with one retry.

    Generation on this hardware occasionally takes minutes or drops the connection
    outright — a measured property of the remote model host, not of the retrieval being
    tested. A suite that aborts on the first slow answer measures the network instead of
    the change, so a timeout is retried once and then reported as a failed probe rather
    than crashing the run and losing every result gathered so far.
    """
    for attempt in (1, 2):
        try:
            response = client.post(
                f"{BASE}/api/chat", json={"question": question}, timeout=300
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if attempt == 2:
                print(f"        (no answer after two attempts: {type(exc).__name__})")
                return {"sources": [], "validation": {}, "grounded": False, "_error": str(exc)}
    return {}


def sections_of(answer: dict) -> list[str]:
    return [s.get("section", "") for s in answer.get("sources", [])]


# ---------------------------------------------------------------------------
# What the corpus actually holds — computed, never assumed
# ---------------------------------------------------------------------------


def richest_dated_document() -> tuple[str, str, list[str], list[str]]:
    """The document with the most dated sections, and how its sections sort by date.

    Returns (document_id, filename, **every** section carrying the newest date, all
    dated section paths newest first). Chosen by counting rather than by name so that
    the suite follows the corpus.

    The newest date is deliberately a set rather than one section. Six sections of this
    corpus carry 13/08/2026 — the ruling, the timeline, the conversation log and others
    all record the same day — and demanding one particular one of them made the test
    depend on how ties happened to order rather than on whether the question reached the
    newest evidence, which is the property being tested. It passed and failed on
    successive builds without the capability changing.
    """
    with sqlite3.connect(str(DB)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "select document_id, section, content, filename from chunks"
        ).fetchall()

    per_document: dict[str, dict[str, tuple[int, int, int]]] = {}
    filenames: dict[str, str] = {}
    for row in rows:
        filenames[row["document_id"]] = row["filename"]
        dates = all_dates(row["section"])
        if not dates:
            continue
        section = row["section"]
        latest = max(dates)
        bucket = per_document.setdefault(row["document_id"], {})
        if latest > bucket.get(section, (0, 0, 0)):
            bucket[section] = latest

    if not per_document:
        return "", "", "", []

    document_id = max(per_document, key=lambda d: len(per_document[d]))
    ordered = sorted(per_document[document_id].items(), key=lambda kv: kv[1], reverse=True)
    paths = [section for section, _ in ordered]
    latest = ordered[0][1]
    newest = [section for section, when in ordered if when == latest]
    return document_id, filenames[document_id], newest, paths


def tail_of(section: str) -> str:
    return section.split("→")[-1].strip()


def mentions(sections: list[str], targets: list[str] | str) -> bool:
    """Whether the retrieved set contains any of the target sections, however nested."""
    wanted = [targets] if isinstance(targets, str) else targets
    haystack = [normalize(s) for s in sections]
    for target in wanted:
        needle = normalize(tail_of(target))[:40]
        if needle and any(needle in s for s in haystack):
            return True
    return False


# ---------------------------------------------------------------------------
# The probes
# ---------------------------------------------------------------------------


LATEST_PHRASINGS = [
    "ما آخر مرحلة وصلت إليها القضية بحسب أحدث تحديث موثق في الملف؟",  # the original failure
    "ما الوضع الحالي للقضية؟",                                          # unseen
    "ما أحدث تطور لدينا في هذا الملف؟",                                 # unseen
    "أين وصلنا الآن في هذا الموضوع؟",                                   # unseen
    "what is the latest stage reached in this matter?",                  # unseen, English
]


def latest_questions_reach_the_newest_section(client: httpx.Client) -> None:
    print("\n-- 1. a 'latest' question retrieves the newest dated section --")

    document_id, filename, newest, dated = richest_dated_document()
    if not newest:
        check(False, "the corpus holds a document with dated sections to test against")
        return
    print(f"        corpus: {filename} — {len(dated)} dated sections")
    print(f"        newest date carried by {len(newest)} section(s):")
    for section in newest[:4]:
        print(f"          · {tail_of(section)[:62]}")

    for question in LATEST_PHRASINGS:
        answer = ask(client, question)
        found = mentions(sections_of(answer), newest)
        check(found, f"newest-dated evidence retrieved: {question[:52]}",
              "top sections: " + " | ".join(tail_of(s)[:34] for s in sections_of(answer)[:5]))


def metadata_does_not_crowd_out_substance(client: httpx.Client) -> None:
    print("\n-- 2. bookkeeping sections no longer take the evidence budget --")

    for question in LATEST_PHRASINGS[:3]:
        answer = ask(client, question)
        sections = sections_of(answer)
        if not sections:
            check(False, f"the question returned evidence: {question[:50]}")
            continue
        bookkeeping = [s for s in sections if classify_section(s).is_metadata]
        share = len(bookkeeping) / len(sections)
        check(
            share <= 0.25,
            f"bookkeeping is at most a quarter of the evidence ({len(bookkeeping)}/{len(sections)})",
            f"{question[:50]} → " + " | ".join(tail_of(s)[:30] for s in bookkeeping),
        )


def metadata_questions_still_reach_metadata(client: httpx.Client) -> None:
    print("\n-- 3. and are still retrieved when the question asks for them --")

    with sqlite3.connect(str(DB)) as connection:
        connection.row_factory = sqlite3.Row
        candidates = [
            row["section"]
            for row in connection.execute("select distinct section from chunks")
            if classify_section(row["section"]).is_metadata
        ]
    if not candidates:
        print("        (no bookkeeping sections in this corpus; nothing to check)")
        return
    print(f"        corpus holds {len(candidates)} bookkeeping section(s)")

    for question in ("ما سجل إصدارات هذا الملف؟", "ما قائمة الملفات والتقارير المنتجة؟"):
        answer = ask(client, question)
        sections = sections_of(answer)
        hit = any(classify_section(s).is_metadata for s in sections)
        check(hit, f"a bookkeeping section is retrieved: {question}",
              " | ".join(tail_of(s)[:34] for s in sections[:5]))


def historical_questions_are_not_dragged_to_the_present(client: httpx.Client) -> None:
    print("\n-- 4. a question about the beginning is not answered from the end --")

    _document_id, _filename, newest, dated = richest_dated_document()
    if len(dated) < 3:
        print("        (too few dated sections to distinguish beginning from end)")
        return
    oldest = dated[-1]

    answer = ask(client, "ما أول إجراء اتُّخذ في بداية هذا الملف؟")
    sections = sections_of(answer)
    check(
        mentions(sections, oldest) or not mentions(sections, newest),
        "the earliest evidence is reached, or at least the newest does not dominate",
        f"oldest={tail_of(oldest)[:40]} | got " + " | ".join(tail_of(s)[:30] for s in sections[:5]),
    )


def conflicts_are_no_longer_noise(client: httpx.Client) -> None:
    print("\n-- 5. conflicts reported are real ones --")

    probes = [
        "ما آخر مرحلة وصلت إليها القضية؟",
        "ما إجراءات المطالبات والتأخير وفق عقد FIDIC؟",
        "ما متطلبات الحفر العميق والتدعيم؟",
    ]
    totals = []
    for question in probes:
        answer = ask(client, question)
        reported = (answer.get("validation") or {}).get("conflicts", [])
        totals.append(len(reported))
        check(
            len(reported) <= 2,
            f"at most two conflicts reported ({len(reported)}): {question[:46]}",
            " || ".join(c[:110] for c in reported[:3]),
        )
    print(f"        conflicts per question: {totals} (the ceiling used to be 12)")


def neutral_questions_are_unchanged(client: httpx.Client) -> None:
    print("\n-- 6. questions with no temporal component still answer as before --")

    for question in (
        "ما إجراءات المطالبات والتأخير وفق عقد FIDIC؟",
        "ما متطلبات الحفر العميق والتدعيم؟",
    ):
        answer = ask(client, question)
        check(bool(answer.get("sources")), f"still grounded: {question[:52]}")
        check(answer.get("grounded") is True, f"still reports grounding: {question[:52]}")


def other_documents_are_unharmed(client: httpx.Client) -> None:
    """The eight documents that carry no dated headings at all.

    Stated plainly because it bounds what this corpus can demonstrate: only one document
    here titles its sections with dates, so the live evidence that recency ranking
    *works* comes from that one file, and the unit suites carry the rest. What these
    probes establish is the other half — that nothing was broken for the documents the
    new signals have nothing to say about, which is most of the corpus and most of the
    questions people actually ask.
    """
    print("\n-- 7. documents with no dated sections are unaffected --")

    unseen = [
        ("ما قيمة الدفعات المستلمة في ملخص المدفوعات؟", "financial summary"),
        ("ما خطوات تقديم مطالبة التأمين؟", "insurance guide"),
        ("ما اشتراطات تدعيم جوانب الحفر؟", "method statement"),
        ("ما ملاحظات المراجعة المسجّلة على المشروع؟", "review observations"),
        # Temporal wording pointed at a document that states no dates in its headings:
        # the signal must degrade to nothing rather than to noise.
        ("ما آخر شروط الدفع المتفق عليها؟", "temporal wording, undated document"),
    ]
    for question, topic in unseen:
        answer = ask(client, question)
        if answer.get("_error"):
            check(False, f"answered: {topic}", answer["_error"][:120])
            continue
        sources = sections_of(answer)
        answered = bool(sources) and answer.get("grounded") is True
        refused = answer.get("answer_source") == "none"
        check(
            answered or refused,
            f"answers or refuses cleanly, never in between: {topic}",
            f"grounded={answer.get('grounded')} sources={len(sources)}",
        )
        conflicts = (answer.get("validation") or {}).get("conflicts", [])
        check(len(conflicts) <= 2, f"no conflict noise: {topic}", str(len(conflicts)))


def main() -> None:
    print(f"live retrieval regression — {BASE}")
    with httpx.Client(timeout=320) as client:
        harness_auth.login(client, BASE)
        latest_questions_reach_the_newest_section(client)
        metadata_does_not_crowd_out_substance(client)
        metadata_questions_still_reach_metadata(client)
        historical_questions_are_not_dragged_to_the_present(client)
        conflicts_are_no_longer_noise(client)
        neutral_questions_are_unchanged(client)
        other_documents_are_unharmed(client)

    print("\n" + "=" * 66)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("the failing class of questions now reaches the evidence that answers it")


if __name__ == "__main__":
    main()
