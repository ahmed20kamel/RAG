"""A Word document uploaded, indexed, retrieved and cited — on the running system.

The parser suite proves the heading tree is built correctly. This proves it is worth
building: that a question about payment terms finds the section called payment terms, and
that the citation says so.

The last test is the one Phase 2 adds that Phase 1 could not: a question whose answer
needs both a Word document and a workbook. Cross-format retrieval is the claim the whole
normalisation argument rests on — if the pipeline really does stop caring what the source
was, a single question should be able to draw on both without anything special happening.

Run: python tests/test_docx_end_to_end.py   (needs the app, Qdrant and Ollama running)
"""

from __future__ import annotations

import os
import sys
import time
import uuid
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

TERMINAL = {
    "completed", "failed", "unsupported_format", "failed_extraction",
    "failed_parsing", "ocr_required", "failed_embedding",
}


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def upload(client: httpx.Client, name: str, category: str = "عقود") -> dict:
    with (CORPUS / name).open("rb") as handle:
        response = client.post(
            f"{BASE}/api/documents/upload",
            files={"file": (name, handle, "application/octet-stream")},
            data={"category": category},
        )
    response.raise_for_status()
    created = response.json()
    UPLOADED.append(created["id"])
    return created


def wait_for(client: httpx.Client, document_id: str, timeout: float = 300) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.get(f"{BASE}/api/documents/{document_id}").json()
        if last.get("status") in TERMINAL:
            return last
        time.sleep(2)
    return last


def ask(client: httpx.Client, question: str) -> dict:
    return client.post(f"{BASE}/api/chat", json={"question": question}).json()


def a_word_document_is_accepted(client: httpx.Client) -> str:
    print("\n-- 1. a Word document uploads and reaches the index --")
    created = upload(client, "styled_headings.docx")
    settled = wait_for(client, created["id"])

    check(settled["status"] == "completed", f"completed ({settled['status']}: {settled.get('error_message')})")
    check(settled["file_type"] == "docx", f"typed as docx ({settled.get('file_type')})")
    check(settled["chunk_count"] > 0, f"chunks were produced ({settled['chunk_count']})")
    check(settled["title"] == "عقد المقاولة", f"the Word title was used ({settled['title']})")
    return created["id"]


def the_heading_tree_reached_the_database(client: httpx.Client, document_id: str) -> None:
    print("\n-- 2. the heading tree is stored, not flattened --")
    sections = client.get(f"{BASE}/api/documents/{document_id}/sections").json()
    headings = {s["heading"] for s in sections}

    check("شروط الدفع" in headings, f"the sections are real headings ({sorted(headings)})")
    payment = next(s for s in sections if s["heading"] == "شروط الدفع")
    check(payment["level"] == 2, "nesting survived")
    check(payment["has_table"], "and the table under it was noticed")


def a_question_finds_its_section_and_cites_it(client: httpx.Client) -> None:
    print("\n-- 3. a question retrieves its own section --")
    answer = ask(client, "ما نسبة الدفعة الثانية وما مبلغها في عقد المقاولة؟")

    check(answer["grounded"], "answered rather than refused")
    check("45" in answer["answer"], "the percentage is right")
    check("1206000" in answer["answer"] or "1,206,000" in answer["answer"], "and the amount")

    mine = [s for s in answer.get("sources", []) if s["filename"] == "styled_headings.docx"]
    check(bool(mine), "the Word document is cited")
    if mine:
        check(
            any("شروط الدفع" in s.get("section", "") for s in mine),
            f"to the section it came from ({[s.get('section') for s in mine][:2]})",
        )
        check(
            any(s.get("locator", "").startswith("جدول") for s in mine),
            f"and the table is named ({[s.get('locator') for s in mine][:2]})",
        )


def a_different_section_answers_a_different_question(client: httpx.Client) -> None:
    print("\n-- 4. a neighbouring section is not confused with it --")
    answer = ask(client, "كم مدة الإخطار الخطي لإنهاء العقد؟")
    check(answer["grounded"], "answered")
    check("أربعة عشر" in answer["answer"] or "14" in answer["answer"], "the notice period is right")
    check(
        "1206000" not in answer["answer"],
        "and the payment table did not leak into it",
    )


def a_document_with_no_heading_styles_still_answers(client: httpx.Client) -> None:
    print("\n-- 5. a document whose headings are only bold still answers by section --")
    created = upload(client, "bold_headings.docx", category="تقارير")
    settled = wait_for(client, created["id"])
    check(settled["status"] == "completed", f"completed ({settled['status']})")

    sections = client.get(f"{BASE}/api/documents/{created['id']}/sections").json()
    check(len(sections) >= 3, f"it did not collapse into one section ({len(sections)})")

    answer = ask(client, "ما الرقم المرجعي الداخلي المعتمد؟")
    check("OPS-2026-4471" in answer["answer"], "and a value inside it is retrievable")


def one_question_draws_on_word_and_excel(client: httpx.Client) -> None:
    """The claim the normalised model was argued for."""
    print("\n-- 6. a single question answered from a Word file and a workbook --")
    workbook = upload(client, "financial_summary.xlsx", category="مالية")
    settled = wait_for(client, workbook["id"])
    check(settled["status"] == "completed", f"the workbook indexed ({settled['status']})")

    answer = ask(
        client,
        "قارن بين مبلغ الدفعة الأولى في عقد المقاولة وقيمة العقد لمشروع أوريون.",
    )
    check(answer["grounded"], "the comparison was answered")

    filenames = {s["filename"] for s in answer.get("sources", [])}
    check(
        "styled_headings.docx" in filenames and "financial_summary.xlsx" in filenames,
        f"both formats are cited ({sorted(filenames)})",
    )

    locators = {
        s["filename"]: s.get("locator", "") for s in answer.get("sources", [])
        if s["filename"] in {"styled_headings.docx", "financial_summary.xlsx"}
    }
    check(
        locators.get("financial_summary.xlsx", "").startswith("Sheet: "),
        f"the workbook is cited by sheet ({locators})",
    )
    check(
        "804000" in answer["answer"] or "804,000" in answer["answer"],
        "the Word figure is in the answer",
    )
    check(
        "1450000" in answer["answer"] or "1,450,000" in answer["answer"],
        "and so is the workbook figure",
    )


def markdown_still_answers_as_before(client: httpx.Client) -> None:
    print("\n-- 7. the Markdown corpus is still untouched --")
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    check(answer["grounded"], "the contract question still answers")
    check("B1N-2023-004410-P01" in answer["answer"], "with the same value as always")
    check(
        all(
            not s.get("locator")
            for s in answer.get("sources", [])
            if s["filename"].endswith(".md")
        ),
        "and Markdown citations still carry no locator",
    )


def the_raw_view_is_text(client: httpx.Client, document_id: str) -> None:
    print("\n-- 8. the source view shows text, not the container --")
    raw = client.get(f"{BASE}/api/documents/{document_id}/raw").json()
    check("PK\x03\x04" not in raw["content"][:20], "not the zip bytes")
    check("شروط الدفع" in raw["content"], "the normalised text, with its headings")


def a_workbook_renamed_docx_is_refused(client: httpx.Client) -> None:
    print("\n-- 9. a workbook renamed .docx is refused on its bytes --")
    created = upload(client, "renamed_xlsx.docx")
    settled = wait_for(client, created["id"])
    check(
        settled["status"] == "unsupported_format",
        f"unsupported_format (got {settled['status']})",
    )


def a_corrupt_document_fails_as_such(client: httpx.Client) -> None:
    print("\n-- 10. a truncated Word file lands in failed_parsing --")
    created = upload(client, "corrupt.docx")
    settled = wait_for(client, created["id"])
    check(settled["status"] == "failed_parsing", f"failed_parsing (got {settled['status']})")


def an_empty_document_says_so(client: httpx.Client) -> None:
    print("\n-- 11. an empty Word file lands in failed_extraction --")
    created = upload(client, "empty.docx")
    settled = wait_for(client, created["id"])
    check(
        settled["status"] == "failed_extraction",
        f"failed_extraction (got {settled['status']})",
    )


def a_viewer_cannot_upload(client: httpx.Client) -> None:
    print("\n-- 12. uploading still needs the capability --")
    email = f"docx-viewer-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    made = client.post(
        f"{BASE}/api/users", json={"email": email, "password": password, "role": "viewer"}
    )
    user_id = made.json()["id"]
    try:
        with httpx.Client(timeout=300) as viewer:
            viewer.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
            with (CORPUS / "styled_headings.docx").open("rb") as handle:
                blocked = viewer.post(
                    f"{BASE}/api/documents/upload",
                    files={"file": ("x.docx", handle, "application/octet-stream")},
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
        document_id = a_word_document_is_accepted(client)
        the_heading_tree_reached_the_database(client, document_id)
        a_question_finds_its_section_and_cites_it(client)
        a_different_section_answers_a_different_question(client)
        a_document_with_no_heading_styles_still_answers(client)
        one_question_draws_on_word_and_excel(client)
        markdown_still_answers_as_before(client)
        the_raw_view_is_text(client, document_id)
        a_workbook_renamed_docx_is_refused(client)
        a_corrupt_document_fails_as_such(client)
        an_empty_document_says_so(client)
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
    print("DOCX ingestion, retrieval, citation and cross-format retrieval hold.")
