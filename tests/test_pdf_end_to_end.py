"""A PDF uploaded, indexed, retrieved and cited by page — on the running system.

The page citation is what this phase is for. A reader given "صفحة 2" can open the file and
check the sentence; that is the difference between a citation and a gesture.

The last test is the one all three phases were building towards: a single question whose
answer needs a PDF, a Word document and a workbook at once. If normalisation really did
its job, nothing special happens — the pipeline does not know or care which was which.

Run: python tests/test_pdf_end_to_end.py   (needs the app, Qdrant and Ollama running)
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


def upload(client: httpx.Client, name: str, category: str = "تقارير") -> dict:
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


def wait_for(client: httpx.Client, document_id: str, timeout: float = 600) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.get(f"{BASE}/api/documents/{document_id}").json()
        if last.get("status") in TERMINAL:
            return last
        time.sleep(3)
    return last


def ask(client: httpx.Client, question: str) -> dict:
    return client.post(f"{BASE}/api/chat", json={"question": question}).json()


def a_pdf_is_accepted_and_indexed(client: httpx.Client) -> str:
    print("\n-- 1. a PDF uploads and reaches the index --")
    created = upload(client, "structured.pdf")
    settled = wait_for(client, created["id"])

    check(settled["status"] == "completed", f"completed ({settled['status']}: {settled.get('error_message')})")
    check(settled["file_type"] == "pdf", f"typed as pdf ({settled.get('file_type')})")
    check(settled["chunk_count"] > 0, f"chunks were produced ({settled['chunk_count']})")
    check(settled["extra_metadata"].get("parser") == "pdf", "the PDF parser handled it")
    check(settled["extra_metadata"].get("pages") == 3, f"three pages recorded")
    return created["id"]


def sections_carry_their_pages(client: httpx.Client, document_id: str) -> None:
    print("\n-- 2. the stored sections know their pages --")
    sections = client.get(f"{BASE}/api/documents/{document_id}/sections").json()
    headings = [s["heading"] for s in sections]
    check(len(sections) >= 3, f"the heading tree survived ({len(sections)} sections)")
    check(
        any("القسم الثاني" in h for h in headings),
        f"with readable Arabic headings ({headings})",
    )


def a_question_is_answered_with_a_page_citation(client: httpx.Client) -> None:
    print("\n-- 3. an answer cites the page it came from --")
    answer = ask(client, "ما رقم أمر المباشرة في تقرير المشروع السنوي؟")

    check(answer["grounded"], "answered rather than refused")
    check("CMN-2026-0817" in answer["answer"], "the reference number is right")

    mine = [s for s in answer.get("sources", []) if s["filename"] == "structured.pdf"]
    check(bool(mine), "the PDF is cited")
    if mine:
        check(
            any(s.get("locator") == "صفحة 2" for s in mine),
            f"and the citation names page 2 ({[s.get('locator') for s in mine]})",
        )
        check(
            any(s.get("page") == 2 for s in mine),
            "the page number is carried as a number too",
        )


def a_value_on_another_page_is_not_confused(client: httpx.Client) -> None:
    print("\n-- 4. a value from a different page is cited to that page --")
    answer = ask(client, "ما قيمة ضمان حسن التنفيذ؟")
    check(answer["grounded"], "answered")
    check("268,000" in answer["answer"] or "268000" in answer["answer"], "the figure is right")

    mine = [s for s in answer.get("sources", []) if s["filename"] == "structured.pdf"]
    if mine:
        check(
            any(s.get("locator") == "صفحة 3" for s in mine),
            f"cited to page 3 ({[s.get('locator') for s in mine]})",
        )


def a_scanned_pdf_is_read_and_flagged(client: httpx.Client) -> None:
    print("\n-- 5. a scanned PDF is read by OCR and marked as such --")
    created = upload(client, "scanned.pdf")
    settled = wait_for(client, created["id"])

    check(settled["status"] == "completed", f"completed ({settled['status']})")
    check(
        settled["extra_metadata"].get("ocr_pages") == 3,
        f"all three pages were OCR'd ({settled['extra_metadata'].get('ocr_pages')})",
    )
    check(
        bool(settled["extra_metadata"].get("extraction_warning")),
        "and the document records that its text came from OCR",
    )


def one_question_draws_on_all_three_formats(client: httpx.Client) -> None:
    """What the normalised model was for."""
    print("\n-- 6. one question answered from a PDF, a Word file and a workbook --")
    for name, category in (
        ("styled_headings.docx", "عقود"),
        ("financial_summary.xlsx", "مالية"),
    ):
        created = upload(client, name, category)
        settled = wait_for(client, created["id"])
        check(settled["status"] == "completed", f"{name} indexed ({settled['status']})")

    answer = ask(
        client,
        "اجمع لي: المساحة الإجمالية المعتمدة في تقرير المشروع، ومبلغ الدفعة الأولى "
        "في عقد المقاولة، وقيمة العقد لمشروع أوريون.",
    )
    check(answer["grounded"], "the three-part question was answered")

    filenames = {s["filename"] for s in answer.get("sources", [])}
    check(
        {"structured.pdf", "styled_headings.docx", "financial_summary.xlsx"} <= filenames,
        f"all three formats are cited ({sorted(filenames)})",
    )

    locators = {
        s["filename"]: s.get("locator", "")
        for s in answer.get("sources", [])
        if s["filename"] in filenames
    }
    check(locators.get("structured.pdf", "").startswith("صفحة"), f"the PDF by page ({locators})")
    check(
        locators.get("financial_summary.xlsx", "").startswith("Sheet: "),
        "the workbook by sheet",
    )

    text = answer["answer"]
    check("4,250" in text or "4250" in text, "the PDF figure is in the answer")
    check("804000" in text or "804,000" in text, "the Word figure too")
    check("2680000" in text or "2,680,000" in text, "and the workbook figure")


def markdown_still_answers_as_before(client: httpx.Client) -> None:
    print("\n-- 7. the Markdown corpus is still untouched --")
    answer = ask(client, "ما رقم العقد المعتمد للمشروع؟")
    check(answer["grounded"], "the contract question still answers")
    check("B1N-2024-005221-P01" in answer["answer"], "with the same value as always")
    check(
        all(
            not s.get("locator")
            for s in answer.get("sources", [])
            if s["filename"].endswith(".md")
        ),
        "and Markdown citations still carry no locator",
    )


def the_raw_view_is_text(client: httpx.Client, document_id: str) -> None:
    print("\n-- 8. the source view shows text, not the PDF bytes --")
    raw = client.get(f"{BASE}/api/documents/{document_id}/raw").json()
    check("%PDF" not in raw["content"][:20], "not the PDF header")
    check("نطاق الأعمال" in raw["content"], "the normalised Arabic text")


def a_corrupt_pdf_fails_as_such(client: httpx.Client) -> None:
    print("\n-- 9. a truncated PDF lands in failed_parsing --")
    created = upload(client, "corrupt.pdf")
    settled = wait_for(client, created["id"])
    check(settled["status"] == "failed_parsing", f"failed_parsing (got {settled['status']})")


def a_word_file_renamed_pdf_is_refused(client: httpx.Client) -> None:
    print("\n-- 10. a Word file renamed .pdf is refused on its bytes --")
    created = upload(client, "renamed_docx.pdf")
    settled = wait_for(client, created["id"])
    check(
        settled["status"] == "unsupported_format",
        f"unsupported_format (got {settled['status']})",
    )


def a_viewer_cannot_upload(client: httpx.Client) -> None:
    print("\n-- 11. uploading still needs the capability --")
    email = f"pdf-viewer-{uuid.uuid4().hex[:8]}@local"
    password = f"pw-{uuid.uuid4().hex[:16]}"
    made = client.post(
        f"{BASE}/api/users", json={"email": email, "password": password, "role": "viewer"}
    )
    user_id = made.json()["id"]
    try:
        with httpx.Client(timeout=300) as viewer:
            viewer.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
            with (CORPUS / "structured.pdf").open("rb") as handle:
                blocked = viewer.post(
                    f"{BASE}/api/documents/upload",
                    files={"file": ("x.pdf", handle, "application/octet-stream")},
                )
            check(blocked.status_code == 403, f"a viewer is refused ({blocked.status_code})")
    finally:
        client.delete(f"{BASE}/api/users/{user_id}")


def cleanup(client: httpx.Client) -> None:
    for document_id in UPLOADED:
        client.delete(f"{BASE}/api/documents/{document_id}")


if __name__ == "__main__":
    client = httpx.Client(timeout=1800)
    harness_auth.login_admin(client, BASE)
    try:
        document_id = a_pdf_is_accepted_and_indexed(client)
        sections_carry_their_pages(client, document_id)
        a_question_is_answered_with_a_page_citation(client)
        a_value_on_another_page_is_not_confused(client)
        a_scanned_pdf_is_read_and_flagged(client)
        one_question_draws_on_all_three_formats(client)
        markdown_still_answers_as_before(client)
        the_raw_view_is_text(client, document_id)
        a_corrupt_pdf_fails_as_such(client)
        a_word_file_renamed_pdf_is_refused(client)
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
    print("PDF ingestion, page citation and four-format retrieval hold.")
