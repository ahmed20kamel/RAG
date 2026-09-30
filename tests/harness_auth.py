"""Signs a test client in as the service account.

The harnesses are real clients of a protected API, so they authenticate the same way a
browser does. The service account exists for exactly this and carries only `chat.ask`,
`document.read` and `system.read` — a leaked harness credential cannot teach the system
anything or delete a document.
"""

from __future__ import annotations

import os
import pathlib
import sys

import httpx

ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"


def env_value(key: str) -> str:
    """Reads a key from .env without importing app config, which the harnesses avoid."""
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def credentials() -> tuple[str, str]:
    email = os.environ.get("RAG_SERVICE_EMAIL") or env_value("SERVICE_ACCOUNT_EMAIL")
    password = os.environ.get("RAG_SERVICE_PASSWORD") or env_value("SERVICE_ACCOUNT_PASSWORD")
    return email, password


def login(client: httpx.Client, base: str) -> dict:
    """Authenticates `client` in place. Its cookie jar carries the session afterwards."""
    email, password = credentials()
    if not email or not password:
        print(
            "No service credentials. Set SERVICE_ACCOUNT_EMAIL and SERVICE_ACCOUNT_PASSWORD\n"
            "in .env and run: python -m app.cli bootstrap",
            file=sys.stderr,
        )
        raise SystemExit(2)

    response = client.post(f"{base}/api/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        print(f"Service login failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
        raise SystemExit(2)
    return response.json()


def admin_credentials() -> tuple[str, str]:
    email = os.environ.get("RAG_ADMIN_EMAIL") or env_value("BOOTSTRAP_ADMIN_EMAIL")
    password = os.environ.get("RAG_ADMIN_PASSWORD") or env_value("BOOTSTRAP_ADMIN_PASSWORD")
    return email, password


def login_admin(client: httpx.Client, base: str) -> dict:
    """For suites that manage documents. The service account deliberately cannot upload,
    so a test that ingests a file has to present an identity that may."""
    email, password = admin_credentials()
    if not email or not password:
        print(
            "No admin credentials. Set BOOTSTRAP_ADMIN_EMAIL and "
            "BOOTSTRAP_ADMIN_PASSWORD in .env, then run: python -m app.cli bootstrap",
            file=sys.stderr,
        )
        raise SystemExit(2)

    response = client.post(f"{base}/api/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        print(f"Admin login failed ({response.status_code}): {response.text[:200]}", file=sys.stderr)
        raise SystemExit(2)
    return response.json()
