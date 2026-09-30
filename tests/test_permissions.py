"""Authentication and authorisation, against the running API.

These assert the two properties the whole knowledge layer will rest on: nothing answers
without an identity, and an identity only reaches what its role carries.

Run: python tests/test_permissions.py   (needs the app running)
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


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def admin_client() -> httpx.Client:
    """Signs in as the bootstrap administrator, read from .env."""
    email = os.environ.get("RAG_ADMIN_EMAIL") or harness_auth.env_value("BOOTSTRAP_ADMIN_EMAIL")
    password = os.environ.get("RAG_ADMIN_PASSWORD") or harness_auth.env_value(
        "BOOTSTRAP_ADMIN_PASSWORD"
    )
    client = httpx.Client(timeout=60)
    response = client.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        print(f"admin login failed: {response.status_code} {response.text[:160]}", file=sys.stderr)
        raise SystemExit(2)
    return client


def anonymous_is_refused() -> None:
    print("\n-- every route needs an identity --")
    with httpx.Client(timeout=60) as client:
        routes = (
            ("GET", "/api/health", None),
            ("GET", "/api/config", None),
            ("GET", "/api/documents", None),
            ("GET", "/api/documents/stats", None),
            ("POST", "/api/chat", {"question": "سؤال اختباري"}),
            ("POST", "/api/chat/stream", {"question": "سؤال اختباري"}),
            ("GET", "/api/users", None),
            ("GET", "/api/teams", None),
        )
        for method, path, body in routes:
            response = client.request(method, BASE + path, json=body)
            check(response.status_code == 401, f"{method} {path} refused without a session")


def bad_credentials_are_refused() -> None:
    print("\n-- credentials are actually checked --")
    email, _password = harness_auth.credentials()
    with httpx.Client(timeout=60) as client:
        wrong = client.post(
            f"{BASE}/api/auth/login", json={"email": email, "password": "not-the-password"}
        )
        check(wrong.status_code == 401, "a wrong password is refused")

        unknown = client.post(
            f"{BASE}/api/auth/login", json={"email": "ghost@nowhere.test", "password": "x" * 12}
        )
        check(unknown.status_code == 401, "an unknown account is refused")
        # Identical replies: a different message would turn this into an account enumerator.
        check(
            wrong.json().get("detail") == unknown.json().get("detail"),
            "wrong password and unknown account are indistinguishable",
        )


def service_account_is_least_privileged() -> None:
    print("\n-- the service account can ask, and nothing else --")
    with httpx.Client(timeout=120) as client:
        me = harness_auth.login(client, BASE)
        check(me["role"] == "service", "service account has the service role")
        check(
            set(me["permissions"]) == {"chat.ask", "document.read", "system.read"},
            f"service permissions are exactly the three read ones ({me['permissions']})",
        )
        check(client.get(f"{BASE}/api/health").status_code == 200, "may read health")
        check(client.get(f"{BASE}/api/documents").status_code == 200, "may list documents")

        upload = client.post(
            f"{BASE}/api/documents/upload",
            files={"file": ("blocked.md", b"# blocked", "text/markdown")},
        )
        check(upload.status_code == 403, "may NOT upload a document")
        check(client.get(f"{BASE}/api/users").status_code == 403, "may NOT list users")
        creation = client.post(f"{BASE}/api/users", json={"email": "x@y.z", "password": "x" * 12})
        check(creation.status_code == 403, "may NOT create a user")


def roles_carry_their_own_capabilities() -> None:
    print("\n-- a role reaches exactly what it carries --")
    admin = admin_client()
    suffix = uuid.uuid4().hex[:8]
    created: list[str] = []

    try:
        expectations = {
            "viewer": {"upload": 403, "users": 403},
            "contributor": {"upload": 202, "users": 403},
        }
        for role, expect in expectations.items():
            email = f"test-{role}-{suffix}@local"
            password = f"pw-{uuid.uuid4().hex[:16]}"
            made = admin.post(
                f"{BASE}/api/users", json={"email": email, "password": password, "role": role}
            )
            check(made.status_code == 201, f"admin can create a {role}")
            created.append(made.json()["id"])

            with httpx.Client(timeout=120) as client:
                login = client.post(
                    f"{BASE}/api/auth/login", json={"email": email, "password": password}
                )
                check(login.status_code == 200, f"{role} can sign in")

                upload = client.post(
                    f"{BASE}/api/documents/upload",
                    files={
                        "file": (
                            f"perm-{role}-{suffix}.md",
                            b"# permission probe\n\nbody text",
                            "text/markdown",
                        )
                    },
                )
                check(upload.status_code == expect["upload"], f"{role} upload -> {expect['upload']}")
                if upload.status_code == 202:
                    admin.delete(f"{BASE}/api/documents/{upload.json()['id']}")

                listing = client.get(f"{BASE}/api/users")
                check(listing.status_code == expect["users"], f"{role} user list -> {expect['users']}")

                # Deleting a document is admin-only, whatever else the role can do.
                removal = client.delete(f"{BASE}/api/documents/does-not-exist")
                check(removal.status_code == 403, f"{role} may NOT delete a document")
    finally:
        for user_id in created:
            admin.delete(f"{BASE}/api/users/{user_id}")
        admin.close()


def sessions_can_be_ended() -> None:
    print("\n-- a session ends when it is told to --")
    with httpx.Client(timeout=60) as client:
        harness_auth.login(client, BASE)
        check(client.get(f"{BASE}/api/auth/me").status_code == 200, "session works before logout")
        check(client.post(f"{BASE}/api/auth/logout").status_code == 204, "logout accepted")
        check(
            client.get(f"{BASE}/api/auth/me").status_code == 401,
            "the same client is refused after logout",
        )


def deactivation_revokes_access() -> None:
    print("\n-- deactivating an account ends its live sessions --")
    admin = admin_client()
    email = f"test-revoke-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    made = admin.post(
        f"{BASE}/api/users", json={"email": email, "password": password, "role": "viewer"}
    )
    check(made.status_code == 201, "created a user to deactivate")
    user_id = made.json()["id"]

    try:
        with httpx.Client(timeout=60) as victim:
            victim.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
            check(victim.get(f"{BASE}/api/auth/me").status_code == 200, "signed in")

            admin.delete(f"{BASE}/api/users/{user_id}")
            # The cookie is still in the jar; it is the row behind it that is gone.
            check(
                victim.get(f"{BASE}/api/auth/me").status_code == 401,
                "the live session stops working the moment the account is disabled",
            )

        again = httpx.post(
            f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=60
        )
        check(again.status_code == 401, "a disabled account cannot sign in again")
    finally:
        admin.close()


if __name__ == "__main__":
    anonymous_is_refused()
    bad_credentials_are_refused()
    service_account_is_least_privileged()
    roles_carry_their_own_capabilities()
    sessions_can_be_ended()
    deactivation_revokes_access()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("All permission checks passed.")
