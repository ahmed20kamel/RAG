"""Live acceptance test. Requires the app, Qdrant and Ollama to be running.

Run: python tests/test_end_to_end.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import os
import httpx

# Run directly as a script, so the project root is not on sys.path yet.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import harness_auth  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

# The API port is configurable because another service may already own 8000.
BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
INSUFFICIENT = "لا توجد معلومات كافية"

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")


def upload(client: httpx.Client, path: Path, category: str) -> str | None:
    with path.open("rb") as handle:
        response = client.post(
            f"{BASE}/api/documents/upload",
            files={"file": (path.name, handle, "text/markdown")},
            data={"category": category},
        )
    if response.status_code == 409:
        listing = client.get(f"{BASE}/api/documents").json()
        existing = next((d for d in listing["items"] if d["filename"] == path.name), None)
        if existing is None:
            return None
        if existing["status"] != "completed":
            client.post(f"{BASE}/api/documents/{existing['id']}/reindex").raise_for_status()
        return existing["id"]
    response.raise_for_status()
    return response.json()["id"]


def wait_for_completion(client: httpx.Client, document_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    seen: list[str] = []
    while time.time() < deadline:
        doc = client.get(f"{BASE}/api/documents/{document_id}?include_chunks=false").json()
        if doc["status"] not in seen:
            seen.append(doc["status"])
        if doc["status"] in ("completed", "failed"):
            print(f"        pipeline: {' → '.join(seen)}")
            return doc
        time.sleep(1)
    raise TimeoutError(f"document {document_id} did not finish in {timeout}s")


def ask(client: httpx.Client, question: str) -> dict:
    response = client.post(f"{BASE}/api/chat", json={"question": question}, timeout=600)
    response.raise_for_status()
    return response.json()


def main() -> None:
    with httpx.Client(timeout=120) as client:
        harness_auth.login_admin(client, BASE)

        health = client.get(f"{BASE}/api/health").json()
        check(health["status"] == "ok", "all dependencies healthy")

        docs = {
            "FIDIC_Claims_Procedure.md": "FIDIC",
            "HSE_Excavation_Method_Statement.md": "HSE",
        }
        ids = {}
        for name, category in docs.items():
            document_id = upload(client, SAMPLES / name, category)
            check(document_id is not None, f"uploaded {name}")
            doc = wait_for_completion(client, document_id)
            check(doc["status"] == "completed", f"{name} reached completed")
            check(doc["chunk_count"] > 0, f"{name} produced {doc['chunk_count']} chunks")
            ids[name] = document_id

        print()
        cases = [
            ("ما هي إجراءات المطالبة بالتعويض عن التأخير؟", True, ["28", "42"], "FIDIC_Claims_Procedure.md"),
            ("كم نسبة غرامة التأخير وما حدها الأقصى؟", True, ["0.05", "10"], "FIDIC_Claims_Procedure.md"),
            ("What is the maximum unsupported excavation depth in loose sand?", True, ["1.2"], "HSE_Excavation_Method_Statement.md"),
            ("متى يجب تركيب حواجز حماية حول الحفريات؟", True, ["2"], "HSE_Excavation_Method_Statement.md"),
            ("ما هو راتب مهندس الموقع في الشركة؟", False, [], None),
        ]

        for question, expect_grounded, must_contain, expected_file in cases:
            result = ask(client, question)
            print(f"\nQ: {question}")
            print(f"   {result['answer'][:280].replace(chr(10), ' ')}")
            for source in result["sources"][:3]:
                print(f"   ↳ {source['filename']} | {source['section']} | {source['score']}")

            if expect_grounded:
                check(result["grounded"], f"grounded answer for: {question[:40]}")
                check(bool(result["sources"]), f"sources returned for: {question[:40]}")
                missing = [token for token in must_contain if token not in result["answer"]]
                check(not missing, f"answer cites facts {must_contain} for: {question[:40]}")
                files = {s["filename"] for s in result["sources"]}
                check(expected_file in files, f"cited the right document for: {question[:40]}")
                check(
                    all(s["section"] for s in result["sources"]),
                    f"every source carries a section for: {question[:40]}",
                )
            else:
                check(not result["grounded"], f"refused out-of-scope: {question[:40]}")
                check(INSUFFICIENT in result["answer"], "refusal uses the exact configured sentence")
                check(result["sources"] == [], "no sources on refusal")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("End-to-end acceptance passed.")


if __name__ == "__main__":
    main()
