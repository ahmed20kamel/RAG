"""The Phase 0 foundation: locators, byte-level type detection, table rendering.

Nothing here parses a real document — no parser exists yet. What is under test is the
machinery every parser will depend on, checked while it is still small enough to reason
about, and in particular the one property the whole plan rests on: that a Markdown
document produces an empty locator and therefore travels the pipeline exactly as it did
before any of this was added.

The table renderer gets the most attention because it is where a wrong answer would look
right. A pipe that is not escaped shifts every value in a row into the neighbouring
column, and a citation to the shifted row would still look perfectly well-formed.

Run: python tests/test_multiformat_foundation.py
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.domain import (  # noqa: E402
    Chunk,
    DocumentStatus,
    FileType,
    Section,
    SourceLocation,
    TERMINAL_FAILURES,
)
from app.exceptions import (  # noqa: E402
    EmbeddingError,
    ExtractionError,
    OcrRequiredError,
    ParsingError,
    UnsupportedFileTypeError,
)
from app.parsers import tables  # noqa: E402
from app.parsers.detection import detect, macro_enabled  # noqa: E402
from app.services.chunking import HeadingAwareChunker  # noqa: E402
from app.services.ingestion import IngestionPipeline  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


# -- 1. locators ----------------------------------------------------------
def locators_read_the_way_a_citation_should() -> None:
    print("\n-- 1. a locator says where, in the words of its own format --")
    check(SourceLocation(page=12).locator() == "صفحة 12", "a PDF page")
    check(
        SourceLocation(page=12, table_index=2).locator() == "صفحة 12 — جدول 2",
        "a table on a PDF page",
    )
    check(
        SourceLocation(sheet="Summary", row_start=12, row_end=18).locator()
        == "Sheet: Summary — صفوف 12–18",
        "a range of spreadsheet rows",
    )
    check(
        SourceLocation(sheet="Summary", row_start=7, row_end=7).locator()
        == "Sheet: Summary — صفوف 7",
        "a single row is not written as a range",
    )
    check(
        SourceLocation(sheet="ملخص").locator() == "Sheet: ملخص",
        "an Arabic sheet name survives intact",
    )
    check(SourceLocation(table_index=2).locator() == "جدول 2", "a DOCX table")
    check(SourceLocation().locator() == "", "and Markdown has no locator at all")


def markdown_chunks_carry_no_locator() -> None:
    """The load-bearing one: this is what makes the existing corpus safe."""
    print("\n-- 2. a section with no location produces chunks with no locator --")
    from app.core.domain import ParsedDocument

    parsed = ParsedDocument(
        title="اختبار",
        sections=[
            Section(heading="بند", level=1, content="نص البند الأول. " * 20, path=["بند"])
        ],
        raw_text="نص",
        language="ar",
    )
    parsed.sections[0].section_id = "s0000"

    chunks = HeadingAwareChunker(600, 80, 100).chunk(parsed, "doc-1", "test.md")
    check(len(chunks) >= 1, f"chunks were produced ({len(chunks)})")
    check(all(c.locator == "" for c in chunks), "every locator is empty")
    check(all(c.page is None for c in chunks), "and no page is claimed")


def a_located_section_passes_it_down() -> None:
    print("\n-- 3. a section that knows where it is passes that to its chunks --")
    from app.core.domain import ParsedDocument

    section = Section(
        heading="الملخص",
        level=1,
        content="قيمة العقد 500,000 درهم. " * 20,
        path=["الملخص"],
        location=SourceLocation(sheet="Summary", row_start=2, row_end=40),
    )
    section.section_id = "s0000"
    parsed = ParsedDocument(
        title="مالي", sections=[section], raw_text="x", language="ar",
        file_type=FileType.XLSX,
    )

    chunks = HeadingAwareChunker(600, 80, 100).chunk(parsed, "doc-2", "f.xlsx")
    check(
        all(c.locator == "Sheet: Summary — صفوف 2–40" for c in chunks),
        "the locator reached every chunk unchanged",
    )


# -- 4. detection ---------------------------------------------------------
def _ooxml(marker: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(marker, "<xml/>")
    return buffer.getvalue()


def bytes_decide_not_the_extension() -> None:
    print("\n-- 4. the bytes decide what a file is --")
    check(detect(b"%PDF-1.7\nrest", ".pdf").file_type == FileType.PDF, "a PDF is a PDF")
    check(
        detect(_ooxml("word/document.xml"), ".docx").file_type == FileType.DOCX,
        "a DOCX is found by the part it declares",
    )
    check(
        detect(_ooxml("xl/workbook.xml"), ".xlsx").file_type == FileType.XLSX,
        "and so is an XLSX",
    )
    check(
        detect("# عنوان\nنص".encode(), ".md").agrees,
        "plain text claiming to be Markdown is taken at its word",
    )


def a_lie_about_the_extension_is_caught() -> None:
    print("\n-- 5. a file renamed to something it is not is refused --")
    check(
        not detect(b"%PDF-1.7\nrest", ".xlsx").agrees,
        "a PDF renamed .xlsx does not agree",
    )
    check(
        not detect(_ooxml("xl/workbook.xml"), ".docx").agrees,
        "a workbook renamed .docx does not agree",
    )
    check(
        not detect(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", ".xlsx").agrees,
        "a legacy .xls renamed .xlsx does not agree",
    )
    check(
        "Office القديمة" in detect(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".xls").detail,
        "and the refusal explains what to do about it",
    )
    check(
        not detect(b"not a zip at all", ".docx").agrees,
        "random bytes named .docx do not agree",
    )


def a_damaged_file_is_told_apart_from_a_wrong_one() -> None:
    """A distinction that changes what a person does next, so it is worth a test."""
    print("\n-- 5b. a damaged container is reported as damaged, not as wrong --")
    truncated = detect(b"PK\x03\x04" + b"\x00" * 200, ".xlsx")
    check(truncated.corrupt, "a truncated archive is flagged corrupt")
    check("تالف" in truncated.detail, "and the message says to re-export it")

    wrong = detect(b"%PDF-1.7\nrest", ".xlsx")
    check(not wrong.corrupt, "a PDF named .xlsx is not corrupt, just wrong")

    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as archive:
        archive.writestr("readme.txt", "hello")
    other = detect(plain_zip.getvalue(), ".docx")
    check(not other.corrupt, "a readable non-Office archive is not corrupt either")


def macros_are_noticed_and_never_run() -> None:
    print("\n-- 6. a macro project is noticed, not executed --")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<xml/>")
        archive.writestr("xl/vbaProject.bin", b"\x00\x01")
    check(macro_enabled(buffer.getvalue()), "the macro project is detected")
    check(not macro_enabled(_ooxml("xl/workbook.xml")), "and a clean workbook is not flagged")


# -- 7. tables ------------------------------------------------------------
def a_pipe_inside_a_cell_cannot_shift_a_row() -> None:
    print("\n-- 7. a cell containing a pipe does not break its row --")
    rendered = tables.render(
        [["البند | الفرعي", "100"]], header=["الوصف", "القيمة"]
    )
    body = [line for line in rendered.splitlines() if line.startswith("|")][-1]
    check(body.count("|") - body.count("\\|") == 3, "the row still has exactly two cells")
    check("\\|" in body, "and the original pipe is escaped, not dropped")


def a_newline_inside_a_cell_does_not_become_a_row() -> None:
    print("\n-- 8. a cell containing a newline stays one row --")
    rendered = tables.render([[tables.clean_cell("سطر\nآخر"), "5"]], header=["أ", "ب"])
    check(len(rendered.splitlines()) == 3, "header, separator, one row")
    check("سطر آخر" in rendered, "the break became a space")


def empty_rows_and_columns_are_dropped() -> None:
    print("\n-- 9. spacer rows and empty columns are removed --")
    grid = tables.normalise_grid(
        [["أ", "", "ب"], ["", "", ""], ["1", "", "2"], ["3", "", "4"]]
    )
    check(len(grid) == 3, f"the blank row is gone ({len(grid)} rows)")
    check(all(len(row) == 2 for row in grid), "and the empty column with it")


def a_header_is_recognised_by_contrast() -> None:
    print("\n-- 10. a header is told apart from data --")
    body = [["1", "100.50"], ["2", "250.00"]]
    check(
        tables.looks_like_header(["الرقم", "المبلغ"], body),
        "text above numbers reads as a header",
    )
    check(
        not tables.looks_like_header(["3", "300.00"], body),
        "a row of numbers does not",
    )


def a_long_table_is_split_with_its_row_numbers() -> None:
    print("\n-- 11. a long table splits, and each part knows its rows --")
    rows = [[str(i), f"{i * 10}"] for i in range(100)]
    groups = tables.split_rows(rows, limit=40)
    check(len(groups) == 3, f"three groups ({len(groups)})")
    check([g[0] for g in groups] == [0, 40, 80], "each starting where the last ended")
    check(sum(len(g[1]) for g in groups) == 100, "and no row was lost")


# -- 12. failure states ---------------------------------------------------
def each_failure_gets_its_own_state() -> None:
    print("\n-- 12. a failure says which kind it was --")
    at = IngestionPipeline._status_for
    check(
        at(OcrRequiredError("x")) == DocumentStatus.OCR_REQUIRED,
        "a scan needing OCR is not just 'failed'",
    )
    check(
        at(ExtractionError("x")) == DocumentStatus.FAILED_EXTRACTION,
        "an empty extraction is its own state",
    )
    check(
        at(UnsupportedFileTypeError("x")) == DocumentStatus.UNSUPPORTED_FORMAT,
        "an unsupported format is its own state",
    )
    check(
        at(ParsingError("x")) == DocumentStatus.FAILED_PARSING,
        "a malformed file is its own state",
    )
    check(
        at(EmbeddingError("x")) == DocumentStatus.FAILED_EMBEDDING,
        "and a failed embedding is too",
    )
    check(
        DocumentStatus.OCR_REQUIRED in TERMINAL_FAILURES
        and DocumentStatus.COMPLETED not in TERMINAL_FAILURES,
        "the terminal set holds the failures and nothing else",
    )


def a_chunk_defaults_to_no_locator() -> None:
    print("\n-- 13. the default Chunk is the Markdown chunk --")
    chunk = Chunk(
        chunk_id="c", document_id="d", filename="f.md", index=0, heading="h",
        section="s", content="x", embed_text="x", heading_level=1, char_count=1,
    )
    check(chunk.locator == "" and chunk.page is None, "no locator, no page")


if __name__ == "__main__":
    locators_read_the_way_a_citation_should()
    markdown_chunks_carry_no_locator()
    a_located_section_passes_it_down()
    bytes_decide_not_the_extension()
    a_lie_about_the_extension_is_caught()
    a_damaged_file_is_told_apart_from_a_wrong_one()
    macros_are_noticed_and_never_run()
    a_pipe_inside_a_cell_cannot_shift_a_row()
    a_newline_inside_a_cell_does_not_become_a_row()
    empty_rows_and_columns_are_dropped()
    a_header_is_recognised_by_contrast()
    a_long_table_is_split_with_its_row_numbers()
    each_failure_gets_its_own_state()
    a_chunk_defaults_to_no_locator()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Phase 0 foundation holds.")
