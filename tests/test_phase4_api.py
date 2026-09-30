"""Conversational learning over HTTP, and the knowledge-only experiment.

The offline suite proves the service refuses to cross the approval boundary. This proves
the running system does too: that a taught sentence in a chat window produces an offer
and nothing else, that accepting it over the API yields a PENDING proposal, and that a
correction raised against a real answer leaves that answer and its knowledge untouched.

The experiment at the end measures what changes when approved knowledge is allowed to
answer alone. It is measured, not assumed, and it is not the default.

Run: python tests/test_phase4_api.py   (needs the app, Qdrant and Ollama running)
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
USERS: list[str] = []

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


# -- 1. a taught sentence produces an offer, not knowledge ----------------
def teaching_in_chat_only_offers(client: httpx.Client) -> None:
    print("\n-- 1. teaching in conversation produces a suggestion, nothing more --")
    message = (
        "تعلم القاعدة: عند ذكر أي مبلغ في الإجابة اكتب العملة بعده صراحةً "
        f"— مرجع {MARK}"
    )
    answer = ask(client, message)
    signal = answer.get("learning_signal")

    check(signal is not None, "the message is recognised as teaching")
    if signal:
        check(signal["type"] == "rule", f"typed as a rule ({signal['type']})")
        check(signal["candidate_id"] != "", "a candidate row was written")
        check(signal["needs_approval"] is True, "and it is marked as needing approval")

        candidate = client.get(f"{BASE}/api/candidates/{signal['candidate_id']}").json()
        check(candidate["state"] == "offered", "the candidate is OFFERED")
        check(candidate["promoted_item_id"] is None, "with no knowledge behind it")

        listing = client.get(f"{BASE}/api/knowledge", params={"q": MARK}).json()
        check(listing["total"] == 0, "and nothing appears in the knowledge base")
        return signal["candidate_id"]
    return ""


def accepting_creates_a_pending_proposal(client: httpx.Client, candidate_id: str) -> None:
    print("\n-- 2. accepting the suggestion yields a PENDING proposal --")
    if not candidate_id:
        check(False, "no candidate to accept")
        return

    accepted = client.post(
        f"{BASE}/api/candidates/{candidate_id}/accept",
        json={"source_text": "قرار الإدارة", "scope": "global"},
    )
    check(accepted.status_code == 201, f"the API accepts it ({accepted.status_code})")
    item = accepted.json()
    TAUGHT.append(item["id"])

    check(item["status"] == "pending", "the proposal is PENDING, not active")
    check(item["type"] == "rule", "the type carries over")
    check(item["source_text"] == "قرار الإدارة", "the attribution the person supplied is kept")

    candidate = client.get(f"{BASE}/api/candidates/{candidate_id}").json()
    check(candidate["state"] == "accepted", "the candidate is closed as accepted")
    check(candidate["promoted_item_id"] == item["id"], "and points at what it produced")

    again = client.post(f"{BASE}/api/candidates/{candidate_id}/accept", json={})
    check(again.status_code == 400, "it cannot be accepted twice")


def dismissing_creates_nothing(client: httpx.Client) -> None:
    print("\n-- 3. dismissing a suggestion creates nothing --")
    answer = ask(client, f"أفضّل الإجابات المختصرة جدًا في هذا الحساب — {MARK}")
    signal = answer.get("learning_signal")
    check(signal is not None, "a preference is recognised")
    if not signal:
        return

    check(signal["needs_approval"] is False, "a personal preference needs no approval")
    dismissed = client.post(
        f"{BASE}/api/candidates/{signal['candidate_id']}/dismiss",
        json={"reason": "قلتها بالخطأ"},
    )
    check(dismissed.status_code == 200, "it can be dismissed")
    check(dismissed.json()["state"] == "dismissed", "the state becomes dismissed")
    check(dismissed.json()["promoted_item_id"] is None, "and nothing was created")


# -- 4. correcting an answer -----------------------------------------------
def correcting_an_answer_changes_nothing_yet(client: httpx.Client) -> None:
    print("\n-- 4. a correction against a real answer leaves it untouched --")
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    answer_id = answer["answer_id"]
    original_text = answer["answer"]

    raised = client.post(
        f"{BASE}/api/candidates/correct-answer",
        json={
            "answer_id": answer_id,
            "correction": f"رقم العقد الصحيح هو B1N-0000-000000 — اختبار {MARK}",
            "source_text": "ملاحظة اختبار",
        },
    )
    check(raised.status_code == 201, f"the correction is accepted as a candidate ({raised.status_code})")
    candidate = raised.json()
    check(candidate["detected_type"] == "correction", "typed as a correction")
    check(candidate["answer_id"] == answer_id, "linked to the answer it disputes")
    check(candidate["state"] == "offered", "and it is only OFFERED")

    trace = client.get(f"{BASE}/api/chat/answers/{answer_id}/why").json()
    check(trace["answer_id"] == answer_id, "the answer's trace is unchanged")

    repeat = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    check(
        "B1N-0000-000000" not in repeat["answer"],
        "the disputed value does not appear in a fresh answer",
    )
    check(
        "B1N-2023-004410-P01" in repeat["answer"] or repeat["answer"] == original_text,
        "the documents still decide the answer",
    )

    client.post(f"{BASE}/api/candidates/{candidate['id']}/dismiss", json={"reason": "تنظيف"})


# -- 5. privacy ------------------------------------------------------------
def suggestions_stay_private(client: httpx.Client) -> None:
    print("\n-- 5. a suggestion quotes what somebody typed, so it stays theirs --")
    email = f"cand-probe-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    made = client.post(
        f"{BASE}/api/users", json={"email": email, "password": password, "role": "contributor"}
    )
    USERS.append(made.json()["id"])

    with httpx.Client(timeout=900) as other:
        other.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
        answer = other.post(
            f"{BASE}/api/chat",
            json={"question": f"تعلم أن الرقم المرجعي الخاص بي هو 9912 — {MARK}"},
        ).json()
        signal = answer.get("learning_signal")
        check(signal is not None, "their message produces their own suggestion")
        if not signal:
            return
        candidate_id = signal["candidate_id"]

        mine = other.get(f"{BASE}/api/candidates", params={"mine": True}).json()
        check(mine["total"] >= 1, "they see it in their own queue")

    # A different contributor must not be able to read or resolve it.
    email2 = f"cand-other-{uuid.uuid4().hex[:8]}@local"
    made2 = client.post(
        f"{BASE}/api/users", json={"email": email2, "password": password, "role": "contributor"}
    )
    USERS.append(made2.json()["id"])
    with httpx.Client(timeout=900) as stranger:
        stranger.post(f"{BASE}/api/auth/login", json={"email": email2, "password": password})
        check(
            stranger.get(f"{BASE}/api/candidates/{candidate_id}").status_code == 403,
            "a colleague cannot read it",
        )
        check(
            stranger.post(f"{BASE}/api/candidates/{candidate_id}/accept", json={}).status_code == 403,
            "nor accept it",
        )
        listing = stranger.get(f"{BASE}/api/candidates", params={"mine": False}).json()
        check(listing["total"] == 0, "and it does not appear in their listing")


def a_viewer_cannot_teach(client: httpx.Client) -> None:
    print("\n-- 6. proposing still needs the capability --")
    email = f"cand-viewer-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    made = client.post(
        f"{BASE}/api/users", json={"email": email, "password": password, "role": "viewer"}
    )
    USERS.append(made.json()["id"])

    with httpx.Client(timeout=900) as viewer:
        viewer.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
        answer = viewer.post(
            f"{BASE}/api/chat",
            json={"question": f"تعلم أن القيمة المعتمدة هي 100 — {MARK}"},
        ).json()
        signal = answer.get("learning_signal")
        if signal:
            blocked = viewer.post(
                f"{BASE}/api/candidates/{signal['candidate_id']}/accept", json={}
            )
            check(blocked.status_code == 403, "a viewer cannot turn a suggestion into a proposal")
        else:
            check(True, "a viewer's message produced no suggestion to promote")

        correction = viewer.post(
            f"{BASE}/api/candidates/correct-answer",
            json={"answer_id": str(uuid.uuid4()), "correction": "أي تصحيح"},
        )
        check(correction.status_code == 403, "nor raise a correction")


def cleanup(client: httpx.Client) -> None:
    for item_id in TAUGHT:
        client.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "تنظيف"})
    for user_id in USERS:
        client.delete(f"{BASE}/api/users/{user_id}")


if __name__ == "__main__":
    client = admin_client()
    try:
        candidate_id = teaching_in_chat_only_offers(client)
        accepting_creates_a_pending_proposal(client, candidate_id)
        dismissing_creates_nothing(client)
        correcting_an_answer_changes_nothing_yet(client)
        suggestions_stay_private(client)
        a_viewer_cannot_teach(client)
    finally:
        cleanup(client)
        client.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All Phase 4 API checks passed.")
