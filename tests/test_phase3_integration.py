"""Phase 3 end to end: semantic knowledge retrieval, conflicts, and why-this-answer.

The offline suites prove the logic in isolation. This proves the running system honours
it: that a claim is found by meaning rather than by shared words, that an ambiguous
disagreement between two real documents is handed back rather than settled, and that the
trace behind an answer names its sources without narrating any reasoning.

Everything taught here is archived at the end, and the test documents are uploaded under
a marker that appears nowhere in the gold corpus.

Run: python tests/test_phase3_integration.py   (needs the app, Qdrant and Ollama)
"""

from __future__ import annotations

import io
import os
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
FAILURES: list[str] = []
TAUGHT: list[str] = []
UPLOADED: list[str] = []

MARK = uuid.uuid4().hex[:6]


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


def teach_and_activate(client: httpx.Client, **body) -> str:
    payload = {"type": "fact", "scope": "global"}
    payload.update(body)
    made = client.post(f"{BASE}/api/knowledge", json=payload)
    made.raise_for_status()
    item_id = made.json()["id"]
    TAUGHT.append(item_id)
    client.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "اختبار"})
    client.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})
    return item_id


def upload(client: httpx.Client, name: str, body: str) -> str:
    made = client.post(
        f"{BASE}/api/documents/upload",
        files={"file": (name, body.encode(), "text/markdown")},
        data={"category": f"ConflictTest{MARK}"},
    )
    made.raise_for_status()
    document_id = made.json()["id"]
    UPLOADED.append(document_id)
    for _ in range(120):
        state = client.get(
            f"{BASE}/api/documents/{document_id}", params={"include_chunks": False}
        ).json()
        if state["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    return document_id


# -- 1. semantic retrieval finds a claim that shares no wording -----------
def semantic_retrieval_finds_by_meaning(client: httpx.Client) -> None:
    print("\n-- 1. an item is found by meaning, not by shared words --")
    item_id = teach_and_activate(
        client,
        content=f"يُمنع تشغيل الرافعات في مشروع زينون {MARK} عندما تتجاوز سرعة الرياح 40 عقدة",
        source_text="تعميم السلامة 7",
    )
    time.sleep(2)  # the vector is written on activation; give the index a moment

    # Phrased with different words: "هل يجوز رفع الأحمال أثناء العواصف" shares almost no
    # stems with the taught sentence, so a lexical match alone would miss it.
    answer = ask(client, f"هل يجوز رفع الأحمال أثناء العواصف في مشروع زينون {MARK}؟")
    used = answer.get("knowledge", [])
    hit = next((k for k in used if k["item_id"] == item_id), None)

    check(hit is not None, "the item is retrieved for a differently worded question")
    if hit:
        check(hit["retrieval"] == "semantic", f"it was found semantically ({hit['retrieval']})")
        check(hit["score"] > 0.0, f"and carries its similarity score ({hit['score']})")
        check(hit["version_id"] != "", "the exact version is identified")
        check(hit["source_text"] == "تعميم السلامة 7", "its source attribution survives")
        check(hit["answer_id"] == answer["answer_id"], "it is linked to this answer")


# -- 2. THE CENTRAL CASE: two real documents, ambiguous authority --------
def ambiguous_documents_are_not_silently_resolved(client: httpx.Client) -> None:
    print("\n-- 2. two documents disagree and nothing entitles either to win --")
    concept = f"بدل السكن الشهري لموظفي مشروع أطلس {MARK}"
    upload(client, f"atlas_a_{MARK}.md", f"# بدل السكن\n\n{concept} هو 7,500 درهم.\n")
    upload(client, f"atlas_b_{MARK}.md", f"# بدل السكن\n\n{concept} هو 9,200 درهم.\n")
    time.sleep(2)

    answer = ask(client, f"كم {concept}؟")
    conflicts = (answer.get("validation") or {}).get("conflicts", [])
    text = answer["answer"]

    check(len(conflicts) > 0, f"the disagreement is reported ({len(conflicts)})")
    both_in_conflict = any(
        ("7,500" in c or "7500" in c) and ("9,200" in c or "9200" in c) for c in conflicts
    )
    check(both_in_conflict, "the conflict note carries both values")
    check(
        ("7,500" in text or "7500" in text) and ("9,200" in text or "9200" in text),
        "and the answer itself states both rather than choosing one",
    )

    trace = client.get(f"{BASE}/api/chat/answers/{answer['answer_id']}/why").json()
    check(trace["conflict_count"] > 0, "the trace records the conflict")
    check(
        trace["unresolved_conflicts"] > 0,
        "and records that nothing settled it, rather than inventing a winner",
    )
    unresolved = [c for c in trace["conflicts"] if not c["resolved"]]
    check(
        any(c["basis"] == "unresolved" for c in unresolved),
        "the recorded basis is explicitly 'unresolved'",
    )


# -- 3. the same pair resolves once authority is declared ----------------
def declared_authority_resolves_the_same_pair(client: httpx.Client) -> None:
    print("\n-- 3. declaring which document is in force settles it, with a reason --")
    # The two documents from case 2 are still indexed; mark one superseded.
    superseded = UPLOADED[-1]
    import sqlite3

    connection = sqlite3.connect(ROOT / "data" / "rag.db")
    connection.execute(
        "update documents set doc_status='superseded' where id=?", (superseded,)
    )
    connection.commit()
    connection.close()

    concept = f"بدل السكن الشهري لموظفي مشروع أطلس {MARK}"
    answer = ask(client, f"كم {concept}؟")
    trace = client.get(f"{BASE}/api/chat/answers/{answer['answer_id']}/why").json()

    resolved = [c for c in trace["conflicts"] if c["resolved"]]
    check(len(resolved) > 0, "the disagreement now resolves")
    if resolved:
        check(resolved[0]["basis"] == "status", f"on the basis of status ({resolved[0]['basis']})")
        check(resolved[0]["winner"] != "", "and the winner is recorded")
        # Values are stored normalised (7500, not 7,500), so both spellings are accepted.
        stated = set(resolved[0]["left"]["values"]) | set(resolved[0]["right"]["values"])
        check(
            {"7500", "9200"} <= stated,
            f"both values are still preserved in the record ({sorted(stated)})",
        )


# -- 4. a rule applies without becoming a fact ---------------------------
def rules_apply_without_being_evidence(client: httpx.Client) -> None:
    print("\n-- 4. a rule shapes the answer without being quotable as a fact --")
    rule_id = teach_and_activate(
        client,
        type="rule",
        content="عند عرض أي مبلغ مالي، اذكر العملة صراحةً بعد الرقم",
    )
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    trace = client.get(f"{BASE}/api/chat/answers/{answer['answer_id']}/why").json()

    policies = trace["policies_applied"]
    check(any(p["item_id"] == rule_id for p in policies), "the rule is recorded as a policy")
    check(
        not any(k["item_id"] == rule_id for k in trace["knowledge_used"]),
        "and NOT as knowledge evidence — the two lists stay separate",
    )
    entry = next((p for p in policies if p["item_id"] == rule_id), None)
    if entry:
        check(entry["influence"] == "applied", "its influence is recorded as 'applied'")
        check(entry["retrieval"] == "directive", "a rule is not retrieved by similarity")


# -- 5. the trace explains without narrating reasoning -------------------
def trace_explains_without_chain_of_thought(client: httpx.Client) -> None:
    print("\n-- 5. the trace is made of evidence, not of reasoning --")
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    trace = client.get(f"{BASE}/api/chat/answers/{answer['answer_id']}/why").json()

    check(trace["answer_id"] == answer["answer_id"], "the trace is addressable by answer id")
    check(len(trace["document_evidence"]) > 0, "document evidence is listed")
    first = trace["document_evidence"][0]
    check(
        all(k in first for k in ("citation", "document_id", "section", "score")),
        "each passage carries its citation, document, section and score",
    )
    check("knowledge_used" in trace, "approved knowledge has its own list")
    check("policies_applied" in trace, "rules and preferences have their own list")
    check(
        not any(key in trace for key in ("reasoning", "thoughts", "chain_of_thought")),
        "nothing resembling chain-of-thought is stored or returned",
    )


# -- 6. another user's trace is not readable -----------------------------
def traces_are_private(client: httpx.Client) -> None:
    print("\n-- 6. a trace carries someone's question, so it stays theirs --")
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")

    email = f"trace-probe-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    made = client.post(
        f"{BASE}/api/users", json={"email": email, "password": password, "role": "viewer"}
    )
    probe_id = made.json()["id"]
    try:
        with httpx.Client(timeout=60) as other:
            other.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
            blocked = other.get(f"{BASE}/api/chat/answers/{answer['answer_id']}/why")
            check(blocked.status_code == 403, "a colleague cannot read someone else's trace")
    finally:
        client.delete(f"{BASE}/api/users/{probe_id}")


# -- 7. archiving removes the vector, not just the row -------------------
def archiving_removes_the_vector(client: httpx.Client) -> None:
    print("\n-- 7. archiving takes the item out of the semantic index too --")
    item_id = teach_and_activate(
        client,
        content=f"مدة صلاحية تصريح الدخول لمشروع نبتون {MARK} تسعون يومًا من الإصدار",
    )
    time.sleep(2)
    question = f"كم تدوم تصاريح الدخول في مشروع نبتون {MARK}؟"

    before = ask(client, question)
    check(
        any(k["item_id"] == item_id for k in before.get("knowledge", [])),
        "the item reaches the answer while active",
    )

    client.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "انتهى الاختبار"})
    time.sleep(2)
    after = ask(client, question)
    check(
        not any(k["item_id"] == item_id for k in after.get("knowledge", [])),
        "and stops reaching it the moment it is archived",
    )


def cleanup(client: httpx.Client) -> None:
    for item_id in TAUGHT:
        client.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "تنظيف"})
    for document_id in UPLOADED:
        client.delete(f"{BASE}/api/documents/{document_id}")


if __name__ == "__main__":
    client = admin_client()
    try:
        semantic_retrieval_finds_by_meaning(client)
        ambiguous_documents_are_not_silently_resolved(client)
        declared_authority_resolves_the_same_pair(client)
        rules_apply_without_being_evidence(client)
        trace_explains_without_chain_of_thought(client)
        traces_are_private(client)
        archiving_removes_the_vector(client)
    finally:
        cleanup(client)
        client.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All Phase 3 integration checks passed.")
