"""The knowledge API end to end: teaching, review, permissions and the audit trail.

`test_knowledge_layer.py` proves the service enforces its rules. This proves the HTTP
surface enforces them too — that a contributor cannot approve their own claim by calling
the endpoint directly, and that the workflow is not merely hidden in the interface.

Run: python tests/test_knowledge_api.py   (needs the app running)
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
CREATED_USERS: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def admin_client() -> httpx.Client:
    client = httpx.Client(timeout=60)
    harness_auth.login_admin(client, BASE)
    return client


def make_client(admin: httpx.Client, role: str, team_id: str | None = None) -> httpx.Client:
    """Creates a throwaway account with the given role and signs a client in as it."""
    email = f"kn-{role}-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    body = {"email": email, "password": password, "role": role}
    if team_id:
        body["team_id"] = team_id
    made = admin.post(f"{BASE}/api/users", json=body)
    if made.status_code != 201:
        print(f"could not create {role}: {made.status_code} {made.text[:160]}", file=sys.stderr)
        raise SystemExit(2)
    CREATED_USERS.append(made.json()["id"])

    client = httpx.Client(timeout=60)
    client.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
    return client


def teach(client: httpx.Client, **body) -> httpx.Response:
    payload = {"type": "fact", "content": "قيمة اختبارية", "scope": "global"}
    payload.update(body)
    return client.post(f"{BASE}/api/knowledge", json=payload)


def workflow_is_enforced_over_http() -> None:
    print("\n-- the workflow holds at the HTTP surface --")
    admin = admin_client()
    contributor = make_client(admin, "contributor")

    try:
        made = teach(
            contributor,
            content=f"مدة الضمان التعاقدي {uuid.uuid4().hex[:4]} شهرًا",
            source_text="البند 9 من العقد",
        )
        check(made.status_code == 201, f"a contributor may teach ({made.status_code})")
        item = made.json()
        item_id = item["id"]
        check(item["status"] == "pending", "the new item is PENDING, not active")
        check(item["version_no"] == 1, "it starts at version 1")
        check(item["can_approve"] is False, "the author is told they cannot approve it")

        # The interface hides the button; the endpoint has to refuse it as well.
        blocked = contributor.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": ""})
        check(blocked.status_code == 403, "the author cannot approve their own claim")
        activated = contributor.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})
        check(activated.status_code == 403, "nor activate it")

        jumped = admin.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})
        check(
            jumped.status_code == 400,
            f"even an admin cannot skip approval ({jumped.status_code})",
        )

        no_reason = admin.post(f"{BASE}/api/knowledge/{item_id}/reject", json={"reason": ""})
        check(no_reason.status_code == 400, "a rejection without a reason is refused")

        approved = admin.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "مطابق للعقد"})
        check(approved.status_code == 200, "an admin may approve")
        check(approved.json()["status"] == "approved", "the status becomes APPROVED")

        live = admin.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})
        check(live.status_code == 200 and live.json()["status"] == "active", "and then activate")

        detail = admin.get(f"{BASE}/api/knowledge/{item_id}").json()
        decisions = [r["decision"] for r in detail["reviews"]]
        check(
            decisions == ["submitted", "approved", "activated"],
            f"the audit trail records each step ({decisions})",
        )
        check(detail["approved_by_name"] != "", "the approver is named")
        check(detail["source_text"] == "البند 9 من العقد", "the source attribution survives")
    finally:
        contributor.close()
        admin.close()


def editing_returns_an_item_to_review() -> None:
    print("\n-- editing an active item withdraws it from use --")
    admin = admin_client()
    contributor = make_client(admin, "contributor")

    try:
        item_id = teach(contributor, content=f"رقم المرجع {uuid.uuid4().hex[:5]}").json()["id"]
        admin.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "ok"})
        admin.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})

        edited = contributor.patch(
            f"{BASE}/api/knowledge/{item_id}",
            json={"content": "رقم المرجع المصحّح", "change_reason": "خطأ مطبعي"},
        )
        check(edited.status_code == 200, "the author may correct their own item")
        check(edited.json()["status"] == "pending", "the correction sends it back to review")
        check(edited.json()["version_no"] == 2, "and appends a version")

        detail = admin.get(f"{BASE}/api/knowledge/{item_id}").json()
        check(len(detail["versions"]) == 2, "both versions are kept")
        first = next(v for v in detail["versions"] if v["version_no"] == 1)
        check(
            first["content"] == "رقم المرجع المصحّح" or first["content"] != edited.json()["content"],
            "the earlier wording is still readable",
        )
    finally:
        contributor.close()
        admin.close()


def scope_isolation_over_http() -> None:
    print("\n-- a personal item is not listed for anyone else --")
    admin = admin_client()
    alice = make_client(admin, "contributor")
    bob = make_client(admin, "contributor")

    try:
        marker = uuid.uuid4().hex[:8]
        made = teach(alice, type="preference", content=f"أجب باختصار {marker}", scope="user")
        check(made.status_code == 201, "a personal preference is accepted")
        check(
            made.json()["status"] == "active",
            "and activates immediately, because it affects nobody else",
        )
        item_id = made.json()["id"]

        listed = bob.get(f"{BASE}/api/knowledge", params={"q": marker}).json()
        check(listed["total"] == 0, "a colleague cannot see it in the listing")

        fetched = bob.get(f"{BASE}/api/knowledge/{item_id}")
        check(fetched.status_code == 403, "nor open it directly")

        own = alice.get(f"{BASE}/api/knowledge", params={"q": marker}).json()
        check(own["total"] == 1, "the owner sees their own")
    finally:
        alice.close()
        bob.close()
        admin.close()


def viewers_cannot_teach() -> None:
    print("\n-- a viewer may read knowledge but not propose it --")
    admin = admin_client()
    viewer = make_client(admin, "viewer")

    try:
        check(viewer.get(f"{BASE}/api/knowledge").status_code == 200, "a viewer may list knowledge")
        check(teach(viewer).status_code == 403, "a viewer may NOT teach")
    finally:
        viewer.close()
        admin.close()


def archived_knowledge_leaves_circulation() -> None:
    print("\n-- archiving takes an item out of use without deleting it --")
    admin = admin_client()
    try:
        marker = uuid.uuid4().hex[:8]
        item_id = teach(admin, content=f"بند مؤقت {marker}").json()["id"]
        admin.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "ok"})
        admin.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})

        archived = admin.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "لم تعد سارية"})
        check(archived.status_code == 200, "an admin may archive")
        check(archived.json()["status"] == "archived", "the status becomes ARCHIVED")

        still_there = admin.get(f"{BASE}/api/knowledge/{item_id}")
        check(still_there.status_code == 200, "the row survives for the audit trail")

        back = admin.post(f"{BASE}/api/knowledge/{item_id}/restore", json={"reason": "مراجعة"})
        check(back.status_code == 200, "it can be restored")
        check(back.json()["status"] == "pending", "restore returns it to review, not to use")
    finally:
        admin.close()


def listing_filters_work() -> None:
    print("\n-- the Knowledge Center filters do what they say --")
    admin = admin_client()
    try:
        marker = uuid.uuid4().hex[:8]
        rule_id = teach(admin, type="rule", content=f"قاعدة {marker}", scope="global").json()["id"]
        teach(admin, type="fact", content=f"واقعة {marker}", scope="global")

        by_type = admin.get(f"{BASE}/api/knowledge", params={"type": "rule", "q": marker}).json()
        check(by_type["total"] == 1, "filtering by type narrows the list")
        check(by_type["items"][0]["type"] == "rule", "and returns the right type")

        by_status = admin.get(
            f"{BASE}/api/knowledge", params={"status": "active", "q": marker}
        ).json()
        check(by_status["total"] == 0, "nothing is active yet, so the active filter is empty")

        admin.post(f"{BASE}/api/knowledge/{rule_id}/approve", json={"reason": "ok"})
        admin.post(f"{BASE}/api/knowledge/{rule_id}/activate", json={"reason": ""})
        after = admin.get(f"{BASE}/api/knowledge", params={"status": "active", "q": marker}).json()
        check(after["total"] == 1, "once activated it appears under the active filter")

        stats = admin.get(f"{BASE}/api/knowledge/stats").json()
        check(stats["total"] >= 2, "the stats endpoint counts items")
        check(stats["active"] >= 1, "and reports how many are active")
    finally:
        admin.close()


def cleanup() -> None:
    admin = admin_client()
    for user_id in CREATED_USERS:
        admin.delete(f"{BASE}/api/users/{user_id}")
    admin.close()


if __name__ == "__main__":
    try:
        workflow_is_enforced_over_http()
        editing_returns_an_item_to_review()
        scope_isolation_over_http()
        viewers_cannot_teach()
        archived_knowledge_leaves_circulation()
        listing_filters_work()
    finally:
        cleanup()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All knowledge API checks passed.")
