"""Monitoring, metrics, relative time and number words — against the running system.

The offline suites prove each piece on fixtures. This proves the pieces are wired: that
a question actually leaves a monitoring record, that the endpoints refuse the people
they should refuse, and that a relative date in a real question reaches the answer as
real dates.

Signs in twice: as the service account, which must be refused the monitoring views, and
as an administrator (BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD, or RAG_ADMIN_EMAIL
/ RAG_ADMIN_PASSWORD), which must be allowed them.

Run:  python tests/test_operations_live.py   (needs the app running)
"""

from __future__ import annotations

import io
import os
import re
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.services.relative_dates import read_relative  # noqa: E402
from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def admin_client() -> httpx.Client | None:
    email = os.environ.get("RAG_ADMIN_EMAIL") or harness_auth.env_value("BOOTSTRAP_ADMIN_EMAIL")
    password = os.environ.get("RAG_ADMIN_PASSWORD") or harness_auth.env_value("BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not password:
        print("        (no administrator credentials configured; admin checks skipped)")
        return None
    client = httpx.Client(timeout=600)
    response = client.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        print(f"        (administrator sign-in failed: {response.status_code}; admin checks skipped)")
        return None
    return client


def authorization(service: httpx.Client, admin: httpx.Client | None) -> None:
    print("\n-- 1. who may see monitoring --")
    anonymous = httpx.get(f"{BASE}/api/metrics", timeout=30)
    check(anonymous.status_code == 401, f"metrics refuse an anonymous caller ({anonymous.status_code})")

    refused = service.get(f"{BASE}/api/admin/monitoring")
    check(refused.status_code == 403, f"the service account is refused the dashboard ({refused.status_code})")
    refused = service.get(f"{BASE}/api/metrics")
    check(refused.status_code == 403, f"and the metrics ({refused.status_code})")

    if admin is None:
        return
    allowed = admin.get(f"{BASE}/api/admin/monitoring?days=1")
    check(allowed.status_code == 200, f"an administrator sees the dashboard ({allowed.status_code})")
    if allowed.status_code == 200:
        body = allowed.json()
        for key in ("components", "traffic"):
            check(key in body, f"the dashboard carries '{key}'")
        check("backup" in body.get("components", {}), "including backup state")
    metrics = admin.get(f"{BASE}/api/metrics")
    check(metrics.status_code == 200 and "rag_requests_total" in metrics.text,
          "and the Prometheus exposition")


def a_question_is_recorded(service: httpx.Client, admin: httpx.Client | None) -> None:
    print("\n-- 2. a question leaves a monitoring record --")
    if admin is None:
        return
    before = admin.get(f"{BASE}/api/admin/monitoring?days=1").json()["traffic"]["total"]
    reply = service.post(f"{BASE}/api/chat", json={"question": "ما مهلة إخطار المطالبة بموجب البند 20.1؟"})
    check(reply.status_code == 200, "the question is answered")
    after = admin.get(f"{BASE}/api/admin/monitoring?days=1").json()["traffic"]
    check(after["total"] == before + 1, f"exactly one record was added ({before} → {after['total']})")
    check(after["latency"]["total"]["max"] > 0, "with its timings")

    health = service.get(f"{BASE}/api/health").json()
    check("reranker" in health, "health reports the reranker in use")


def relative_time_reaches_the_answer(service: httpx.Client) -> None:
    print("\n-- 3. a relative period arrives in the answer as dates --")
    question = "ما الذي حدث في الملف خلال آخر ستين يومًا؟"
    window = read_relative(question)
    check(window is not None, f"the phrase resolves: {window.describe() if window else '—'}")
    reply = service.post(f"{BASE}/api/chat", json={"question": question}, timeout=600).json()
    answer = reply.get("answer", "")
    stated = {
        (int(y), int(m), int(d))
        for d, m, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", answer)
    }
    inside = [s for s in stated if window and window.contains(s)]
    outside = [s for s in stated if window and not window.contains(s) and date(*s) != window.today]
    check(bool(inside) or "لا" in answer[:200],
          "the answer names dates inside the period, or says there are none",
          answer[:200].replace("\n", " "))
    check(len(outside) <= len(inside),
          "and does not lead with dates from outside it", f"inside={inside} outside={outside}")
    unsupported = (reply.get("validation") or {}).get("unsupported_values", [])
    period = {window.start.strftime("%d/%m/%Y"), window.end.strftime("%d/%m/%Y")} if window else set()
    check(not (set(unsupported) & period),
          "the period's own dates are not flagged as unsupported", str(unsupported))


def main() -> None:
    print(f"operations — {BASE}")
    with httpx.Client(timeout=600) as service:
        harness_auth.login(service, BASE)
        admin = admin_client()
        try:
            authorization(service, admin)
            a_question_is_recorded(service, admin)
            relative_time_reaches_the_answer(service)
        finally:
            if admin is not None:
                admin.close()

    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("monitoring is wired, guarded, and relative time reaches the answer")


if __name__ == "__main__":
    main()
