"""A workbook uploaded, indexed, retrieved and cited — over HTTP, on the running system.

The parser suite proves a spreadsheet can be read. This proves the rest of the claim,
which is the part worth doubting: that a figure buried in a sheet can be found by a
question phrased in ordinary Arabic, and that the answer comes back pointing at the sheet
and rows it was taken from.

"What was the total VAT for project X" is the question the whole design was argued
against, so it is the question asked here.

The Markdown corpus is left alone throughout, and checked at the end to be sure: a
workbook must not become the answer to a question about the contract.

Run: python tests/test_xlsx_end_to_end.py   (needs the app, Qdrant and Ollama running)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
CORPUS = ROOT / "tests" / "corpus"
FAILURES: list[str] = []
UPLOADED: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def upload(client: httpx.Client, name: str, category: str = "مالية") -> dict:
    with (CORPUS / name).open("rb") as handle:
        response = client.post(
            f"{BASE}/api/documents/upload",
            files={"file": (name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"category": category},
        )
    response.raise_for_status()
    return response.json()


def wait_for(client: httpx.Client, document_id: str, timeout: float = 300) -> dict:
    """Poll until the pipeline stops moving. Returns whatever state it settled in."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.get(f"{BASE}/api/documents/{document_id}").json()
        if last["status"] in {
            "completed", "failed", "unsupported_format", "failed_extraction",
            "failed_parsing", "ocr_required", "failed_embedding",
        }:
            return last
        time.sleep(2)
    return last


def ask(client: httpx.Client, question: str) -> dict:
    return client.post(f"{BASE}/api/chat", json={"question": question}).json()


def a_workbook_is_accepted_and_indexed(client: httpx.Client) -> str:
    print("\n-- 1. a workbook uploads and reaches the index --")
    created = upload(client, "financial_summary.xlsx")
    document_id = created["id"]
    UPLOADED.append(document_id)

    settled = wait_for(client, document_id)
    check(settled["status"] == "completed", f"it completed ({settled['status']}: {settled.get('error_message')})")
    check(settled["file_type"] == "xlsx", f"typed as xlsx ({settled.get('file_type')})")
    check(settled["chunk_count"] > 0, f"and produced chunks ({settled['chunk_count']})")
    check(
        settled["extra_metadata"].get("parser") == "xlsx",
        "the workbook parser is the one that handled it",
    )
    return document_id


def chunks_carry_their_sheet_and_rows(client: httpx.Client, document_id: str) -> None:
    print("\n-- 2. every stored chunk knows where it came from --")
    page = client.get(f"{BASE}/api/documents/{document_id}/chunks", params={"limit": 50}).json()
    items = page["items"]
    check(len(items) > 0, f"chunks came back ({len(items)})")

    sections = {i.get("section", "") for i in items}
    check(
        any("Summary" in s for s in sections) and any("ملخص المطالبات" in s for s in sections),
        f"sheets are the sections ({sorted(sections)[:3]})",
    )


def a_question_in_arabic_finds_a_figure_in_a_sheet(client: httpx.Client) -> None:
    """The question the design was argued against."""
    print("\n-- 3. an Arabic question retrieves the right row and cites the sheet --")
    answer = ask(client, "ما قيمة ضريبة القيمة المضافة لمشروع أوريون؟")

    check(answer["grounded"], "the question was answered rather than refused")
    check("92000" in answer["answer"] or "92,000" in answer["answer"], f"the VAT figure is in the answer")

    sources = answer.get("sources", [])
    mine = [s for s in sources if s["filename"] == "financial_summary.xlsx"]
    check(bool(mine), f"the workbook is cited ({[s['filename'] for s in sources][:3]})")
    if mine:
        locator = mine[0].get("locator", "")
        check(locator.startswith("Sheet: "), f"the citation names the sheet ({locator!r})")
        check("صفوف" in locator, "and the rows it came from")


def a_second_sheet_is_reachable_too(client: httpx.Client) -> None:
    print("\n-- 4. a different sheet answers a different question --")
    answer = ask(client, "ما المبلغ المطالب به في المطالبة CL-2026-002 وما حالتها؟")
    check(answer["grounded"], "answered")
    check("127300" in answer["answer"] or "127,300" in answer["answer"], "the amount is right")
    check("معتمدة" in answer["answer"], "and so is the status")

    mine = [s for s in answer.get("sources", []) if s["filename"] == "financial_summary.xlsx"]
    if mine:
        check(
            any("ملخص المطالبات" in s.get("locator", "") for s in mine),
            f"cited to the Arabic sheet ({[s.get('locator') for s in mine][:2]})",
        )


def a_figure_is_not_reported_without_its_column(client: httpx.Client) -> None:
    print("\n-- 5. the column a figure sits under reaches the model --")
    answer = ask(client, "كم بلغ المصروف على مشروع نيبتون؟")
    check(answer["grounded"], "answered")
    # 2,015,000 is the spend; 3,920,000 is the contract value on the same row. Returning
    # the wrong one is exactly what happens when a row arrives without its header.
    check(
        "2015000" in answer["answer"] or "2,015,000" in answer["answer"],
        f"the spend column was read, not its neighbour",
    )
    check(
        "3920000" not in answer["answer"] and "3,920,000" not in answer["answer"],
        "and the contract value was not mistaken for it",
    )


def markdown_still_answers_as_before(client: httpx.Client) -> None:
    print("\n-- 6. the Markdown corpus is unaffected --")
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    check(answer["grounded"], "the contract question still answers")
    check("B1N-2024-005221-P01" in answer["answer"], "with the same value as always")

    sources = answer.get("sources", [])
    check(
        all(s["filename"] != "financial_summary.xlsx" for s in sources),
        "and the workbook did not intrude on it",
    )
    check(
        all(not s.get("locator") for s in sources),
        "Markdown citations still carry no locator",
    )


def the_raw_view_shows_text_not_bytes(client: httpx.Client, document_id: str) -> None:
    print("\n-- 7. the source view shows readable text, not the binary --")
    raw = client.get(f"{BASE}/api/documents/{document_id}/raw").json()
    content = raw["content"]
    check(len(content) > 0, "something came back")
    check("PK\x03\x04" not in content[:20], "it is not the zip container")
    check("ضريبة القيمة المضافة" in content, "it is the normalised text")


def a_renamed_pdf_is_refused(client: httpx.Client) -> None:
    print("\n-- 8. a PDF renamed .xlsx is refused on its bytes --")
    created = upload(client, "renamed_pdf.xlsx")
    UPLOADED.append(created["id"])
    settled = wait_for(client, created["id"])
    check(
        settled["status"] == "unsupported_format",
        f"refused as unsupported_format (got {settled['status']})",
    )
    check(
        "لا يطابق امتداده" in (settled.get("error_message") or ""),
        "and the message says the content does not match the extension",
    )


def a_corrupt_workbook_fails_as_such(client: httpx.Client) -> None:
    print("\n-- 9. a truncated workbook lands in failed_parsing --")
    created = upload(client, "corrupt.xlsx")
    UPLOADED.append(created["id"])
    settled = wait_for(client, created["id"])
    check(
        settled["status"] == "failed_parsing",
        f"failed_parsing (got {settled['status']})",
    )


def an_empty_workbook_says_so(client: httpx.Client) -> None:
    print("\n-- 10. an empty workbook lands in failed_extraction --")
    created = upload(client, "empty.xlsx")
    UPLOADED.append(created["id"])
    settled = wait_for(client, created["id"])
    check(
        settled["status"] == "failed_extraction",
        f"failed_extraction (got {settled['status']})",
    )


def a_viewer_cannot_upload(client: httpx.Client) -> None:
    print("\n-- 11. uploading still needs the capability --")
    import uuid

    email = f"xlsx-viewer-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    made = client.post(
        f"{BASE}/api/users", json={"email": email, "password": password, "role": "viewer"}
    )
    user_id = made.json()["id"]
    try:
        with httpx.Client(timeout=300) as viewer:
            viewer.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
            with (CORPUS / "financial_summary.xlsx").open("rb") as handle:
                blocked = viewer.post(
                    f"{BASE}/api/documents/upload",
                    files={"file": ("x.xlsx", handle, "application/octet-stream")},
                )
            check(blocked.status_code == 403, f"a viewer is refused ({blocked.status_code})")
    finally:
        client.delete(f"{BASE}/api/users/{user_id}")


def cleanup(client: httpx.Client) -> None:
    for document_id in UPLOADED:
        client.delete(f"{BASE}/api/documents/{document_id}")


if __name__ == "__main__":
    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)
    try:
        document_id = a_workbook_is_accepted_and_indexed(client)
        chunks_carry_their_sheet_and_rows(client, document_id)
        a_question_in_arabic_finds_a_figure_in_a_sheet(client)
        a_second_sheet_is_reachable_too(client)
        a_figure_is_not_reported_without_its_column(client)
        markdown_still_answers_as_before(client)
        the_raw_view_shows_text_not_bytes(client, document_id)
        a_renamed_pdf_is_refused(client)
        a_corrupt_workbook_fails_as_such(client)
        an_empty_workbook_says_so(client)
        a_viewer_cannot_upload(client)
    finally:
        cleanup(client)
        client.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("XLSX ingestion, retrieval and citation hold end to end.")
