"""The XLSX parser, against a corpus built to have the problems real files have.

What is checked is not "did it produce text" — almost anything produces text. It is
whether the things that make a spreadsheet answerable survived: the column a figure sits
under, the row it sits in, the sheet it came from, and the fact that a number is still
the same number. Each of those is what a citation will later claim, and a claim nobody
checked is the failure mode this suite exists to prevent.

Run: python tests/test_xlsx_parser.py   (build the corpus first)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.domain import BlockType, FileType  # noqa: E402
from app.exceptions import ExtractionError, ParsingError  # noqa: E402
from app.parsers.xlsx_parser import XlsxParser  # noqa: E402

CORPUS = ROOT / "tests" / "corpus"
FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def parse(name: str):
    return XlsxParser().parse((CORPUS / name).read_bytes(), name)


def every_sheet_becomes_its_own_section() -> None:
    print("\n-- 1. each sheet is a section that knows its name --")
    doc = parse("financial_summary.xlsx")
    headings = [s.heading for s in doc.sections]

    check(doc.file_type == FileType.XLSX, "the document is typed as a workbook")
    check("Summary" in headings, "the Summary sheet is there")
    check("Cost Details" in headings, "so is Cost Details")
    check("ملخص المطالبات" in headings, "and the Arabic sheet name survived")
    check(doc.language in ("ar", "mixed"), f"the language is Arabic-ish ({doc.language})")


def a_figure_keeps_the_column_it_belongs_to() -> None:
    """The one that matters: a number without its column name is not an answer."""
    print("\n-- 2. a value stays under the column that names it --")
    doc = parse("financial_summary.xlsx")
    summary = next(s for s in doc.sections if s.heading == "Summary")

    check("ضريبة القيمة المضافة" in summary.content, "the VAT column is named in the text")
    check("92000" in summary.content, "and the VAT figure is present")

    lines = [l for l in summary.content.splitlines() if l.startswith("|")]
    header_line = lines[0]
    orion = next((l for l in lines if "أوريون" in l), "")
    header_cells = [c.strip() for c in header_line.strip("|").split("|")]
    orion_cells = [c.strip() for c in orion.strip("|").split("|")]

    check(len(header_cells) == len(orion_cells), "the row has as many cells as the header")
    if len(header_cells) == len(orion_cells):
        paired = dict(zip(header_cells, orion_cells))
        check(
            paired.get("ضريبة القيمة المضافة") == "92000",
            f"VAT for Orion reads back as 92000 (got {paired.get('ضريبة القيمة المضافة')!r})",
        )
        check(
            paired.get("قيمة العقد") == "2680000",
            "and the contract value is under its own column",
        )


def the_title_rows_are_not_mistaken_for_the_header() -> None:
    print("\n-- 3. the header is found below the title lines --")
    doc = parse("financial_summary.xlsx")
    summary = next(s for s in doc.sections if s.heading == "Summary")
    first = [l for l in summary.content.splitlines() if l.startswith("|")][0]
    check("المشروع" in first, "the column names are the header row")
    check("الملخص المالي" not in first, "not the document title")


def a_note_beside_the_header_does_not_hide_it() -> None:
    print("\n-- 4. an annotation typed beside the header does not break detection --")
    doc = parse("messy_boq.xlsx")
    boq = doc.sections[0]
    lines = [l for l in boq.content.splitlines() if l.startswith("|")]
    check(any("---" in l for l in lines[:3]), "a header row was found at all")
    check("Qty" in lines[0] and "Price" in lines[0], "and it is the real column row")


def broken_formulas_are_dropped_not_indexed() -> None:
    print("\n-- 5. #REF! is not a value --")
    doc = parse("messy_boq.xlsx")
    whole = "\n".join(s.content for s in doc.sections)
    check("#REF!" not in whole, "no #REF! reached the text")
    check("#DIV/0!" not in whole, "and no other error string either")
    check("66432" in whole, "while the real figures beside them survived")


def a_second_table_in_one_sheet_is_split_off() -> None:
    print("\n-- 6. blank rows separate one table from the next --")
    doc = parse("messy_boq.xlsx")
    check(len(doc.sections) >= 2, f"the sheet produced more than one section ({len(doc.sections)})")
    joined = "\n".join(s.content for s in doc.sections)
    check("اسم المقاول من الباطن" in joined, "the label/value table is present")
    check(
        any("|" in s.content and "اسم المقاول" in s.content for s in doc.sections),
        "and it is rendered as rows the fact extractor can read",
    )


def presentation_form_arabic_is_repaired() -> None:
    print("\n-- 7. Arabic stored as display glyphs becomes Arabic letters --")
    doc = parse("presentation_forms.xlsx")
    whole = "\n".join(s.content for s in doc.sections)
    check("نيابة مرور أبو ظبي" in whole, "the text reads as ordinary Arabic")
    check("علاء ثروت محمد" in whole, "the name too")
    check("قضية رقم 200137" in whole, "and the tatweel is gone from 'رقــم'")
    check(
        not any("ﭐ" <= c <= "﻿" for c in whole),
        "no presentation form is left anywhere",
    )


def a_long_sheet_splits_and_keeps_its_row_numbers() -> None:
    print("\n-- 8. a long sheet splits, and every part cites the right rows --")
    doc = parse("large_ledger.xlsx")
    section = doc.sections[0]
    check(len(section.blocks) > 1, f"it split into blocks ({len(section.blocks)})")
    check(
        all(b.type == BlockType.SHEET_RANGE for b in section.blocks),
        "each block is a sheet range",
    )

    starts = [b.location.row_start for b in section.blocks if b.location]
    check(starts == sorted(starts), "the ranges are in order")
    check(starts[0] == 2, f"the first block starts at row 2, below the header (got {starts[0]})")

    last = section.blocks[-1].location
    check(last is not None and last.row_end == 501, f"the last row is 501 (got {last.row_end if last else None})")
    check(
        all("رقم القيد" in b.text for b in section.blocks),
        "and every block repeats the header, so no figure is orphaned",
    )


def locators_say_sheet_and_rows() -> None:
    print("\n-- 9. the locator names the sheet and the rows --")
    doc = parse("financial_summary.xlsx")
    summary = next(s for s in doc.sections if s.heading == "Summary")
    block = summary.blocks[0]
    check(block.location is not None, "the block carries a location")
    if block.location:
        locator = block.location.locator()
        check(locator.startswith("Sheet: Summary"), f"it names the sheet ({locator})")
        check("صفوف" in locator, "and the rows")

    arabic = next(s for s in doc.sections if s.heading == "ملخص المطالبات")
    check(
        arabic.locator.startswith("Sheet: ملخص المطالبات"),
        f"an Arabic sheet name is not mangled ({arabic.locator})",
    )


def structured_data_keeps_what_rendering_flattened() -> None:
    print("\n-- 10. the structured copy is kept beside the rendered one --")
    doc = parse("financial_summary.xlsx")
    block = next(s for s in doc.sections if s.heading == "Summary").blocks[0]
    check(block.structured_data is not None, "structured_data is populated")
    if block.structured_data:
        flat = [cell for row in block.structured_data for cell in row]
        check("2680000" in flat, "a figure is present as its own cell, not inside a line")
        check("ضريبة القيمة المضافة" in flat, "and so is its column name")


def an_empty_workbook_is_refused_clearly() -> None:
    print("\n-- 11. an empty workbook is refused, and says why --")
    try:
        parse("empty.xlsx")
        check(False, "it should not have parsed")
    except ExtractionError as exc:
        check(True, "ExtractionError, which maps to failed_extraction")
        check("فارغة" in str(exc) or "قابلة للفهرسة" in str(exc), "the message explains it")


def a_workbook_of_only_errors_is_refused() -> None:
    print("\n-- 12. a workbook holding nothing but #REF! has nothing to index --")
    try:
        parse("errors_only.xlsx")
        check(False, "it should not have parsed")
    except ExtractionError:
        check(True, "refused rather than indexed as rows of '#REF!'")


def a_corrupt_file_fails_as_a_parse_error() -> None:
    print("\n-- 13. a truncated file is a parsing failure, not a crash --")
    try:
        parse("corrupt.xlsx")
        check(False, "it should not have parsed")
    except ParsingError as exc:
        check(True, "ParsingError, which maps to failed_parsing")
        check("تالف" in str(exc) or "تعذر فتح" in str(exc), "with a message a person can act on")
    except Exception as exc:  # noqa: BLE001
        check(False, f"raised {type(exc).__name__} instead of ParsingError")


if __name__ == "__main__":
    if not (CORPUS / "financial_summary.xlsx").exists():
        print("corpus missing — run tests/corpus/build_xlsx_corpus.py first", file=sys.stderr)
        raise SystemExit(2)

    every_sheet_becomes_its_own_section()
    a_figure_keeps_the_column_it_belongs_to()
    the_title_rows_are_not_mistaken_for_the_header()
    a_note_beside_the_header_does_not_hide_it()
    broken_formulas_are_dropped_not_indexed()
    a_second_table_in_one_sheet_is_split_off()
    presentation_form_arabic_is_repaired()
    a_long_sheet_splits_and_keeps_its_row_numbers()
    locators_say_sheet_and_rows()
    structured_data_keeps_what_rendering_flattened()
    an_empty_workbook_is_refused_clearly()
    a_workbook_of_only_errors_is_refused()
    a_corrupt_file_fails_as_a_parse_error()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("XLSX parsing holds.")
