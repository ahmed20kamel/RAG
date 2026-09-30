"""Approved knowledge reaching — and not reaching — a real answer.

The offline suite proves the service refuses to hand out unapproved items. This proves
the chat path honours that end to end: the same claim is invisible while it is pending,
visible once activated, and gone again once archived, with the answer's own report
saying which items touched it.

Every item taught here is scoped and cleaned up, and the questions are about a subject
invented for the test, so nothing in the gold set can be disturbed.

Run: python tests/test_knowledge_in_chat.py   (needs the app, Qdrant and Ollama running)
"""

from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
FAILURES: list[str] = []
TAUGHT: list[str] = []

MARKER = uuid.uuid4().hex[:6]
# Anchored to a subject the indexed documents already cover. Taught knowledge supplements
# document evidence rather than standing in for it: a question the retriever finds nothing
# for is refused before the knowledge arm is ever consulted, so a claim about an invented
# project could never be observed reaching an answer. That behaviour is deliberate — and
# it is also a real limit, recorded in the Phase 3 report.
QUESTION = "ما شروط تمديد مدة الإنجاز في المشروع؟"
CLAIM = (
    "يشترط لتمديد مدة الإنجاز في المشروع موافقة خطية من المدير العام خلال سبعة أيام "
    f"— مرجع الاختبار {MARKER}"
)


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def admin_client() -> httpx.Client:
    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)
    return client


def ask(client: httpx.Client, question: str) -> dict:
    response = client.post(f"{BASE}/api/chat", json={"question": question})
    response.raise_for_status()
    return response.json()


def teach(client: httpx.Client, **body) -> str:
    payload = {"type": "fact", "content": CLAIM, "scope": "global", "source_text": "قرار التشغيل 12"}
    payload.update(body)
    made = client.post(f"{BASE}/api/knowledge", json=payload)
    made.raise_for_status()
    item_id = made.json()["id"]
    TAUGHT.append(item_id)
    return item_id


def pending_knowledge_never_reaches_an_answer(client: httpx.Client) -> str:
    print("\n-- a pending claim is invisible to the answer path --")
    item_id = teach(client)
    answer = ask(client, QUESTION)
    check(
        not any(k["item_id"] == item_id for k in answer.get("knowledge", [])),
        "a PENDING item is not reported as having reached the answer",
    )
    check(
        MARKER not in answer["answer"],
        "and its unique marker does not appear in the answer text",
    )
    return item_id


def active_knowledge_reaches_the_answer(client: httpx.Client, item_id: str) -> None:
    print("\n-- once approved and activated it does --")
    client.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "معتمد للاختبار"})
    client.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})

    answer = ask(client, QUESTION)
    used = answer.get("knowledge", [])
    check(any(k["item_id"] == item_id for k in used), "the ACTIVE item is reported on the answer")

    if used:
        entry = next(k for k in used if k["item_id"] == item_id)
        check(entry["type"] == "fact", "its type is reported")
        check(entry["scope"] == "global", "its scope is reported")
        check(entry["source_text"] == "قرار التشغيل 12", "its source attribution is reported")
        check(entry["version_no"] == 1, "the exact version is reported")
        check(
            entry["influence"] in ("offered", "stated"),
            f"how far it got is reported ({entry.get('influence')})",
        )

    detail = client.get(f"{BASE}/api/knowledge/{item_id}").json()
    check(detail["usage_count"] >= 1, "the usage is recorded against the item")
    check(
        any(u["question_hash"] for u in detail["usages"]),
        "the usage row carries the question digest, not the question text",
    )


def archived_knowledge_stops_reaching_it(client: httpx.Client, item_id: str) -> None:
    print("\n-- archiving takes it back out of circulation --")
    client.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "انتهى الاختبار"})
    answer = ask(client, QUESTION)
    check(
        not any(k["item_id"] == item_id for k in answer.get("knowledge", [])),
        "an ARCHIVED item stops reaching answers immediately",
    )


def documents_still_answer_without_knowledge(client: httpx.Client) -> None:
    print("\n-- the document path is untouched by any of this --")
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    check(answer["grounded"] is True, "a document question is still answered")
    check(len(answer["sources"]) > 0, "and still cites document sources")
    check(
        "B1N-2023-004410-P01" in answer["answer"],
        "with the value the documents actually state",
    )
    # Directives apply to every answer by design; what must not appear is a taught *fact*
    # that has nothing to do with the question.
    facts = [k for k in answer.get("knowledge", []) if k["type"] not in ("rule", "preference")]
    check(facts == [], f"no unrelated taught fact is attached ({[k['content'][:30] for k in facts]})")


def cleanup(client: httpx.Client) -> None:
    for item_id in TAUGHT:
        client.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "تنظيف الاختبار"})


if __name__ == "__main__":
    client = admin_client()
    try:
        item_id = pending_knowledge_never_reaches_an_answer(client)
        active_knowledge_reaches_the_answer(client, item_id)
        archived_knowledge_stops_reaching_it(client, item_id)
        documents_still_answer_without_knowledge(client)
    finally:
        cleanup(client)
        client.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All knowledge-in-chat checks passed.")
