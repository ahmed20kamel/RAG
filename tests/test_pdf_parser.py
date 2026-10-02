"""The PDF parser, against the properties that make PDFs hard.

Three things here are worth more than the rest, because each fails silently.

Arabic reading order. A PDF can store a sentence laid out rather than written, and the
words come back reversed. The text still looks like Arabic, still indexes, still gets
cited — and says something different from the document.

The scanned refusal. A page of pixels extracts as nothing. Indexed, it becomes a document
that exists, appears healthy in the library, and answers no question ever asked of it.

Page numbers. They are the whole value of a PDF citation, and a locator that points at
the wrong page is worse than none, because it looks checkable.

Run: python tests/test_pdf_parser.py   (build the corpus first)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.domain import BlockType, FileType  # noqa: E402
from app.exceptions import ExtractionError, OcrRequiredError, ParsingError  # noqa: E402
from app.parsers import arabic_pdf, ocr  # noqa: E402
from app.parsers.pdf_parser import PdfParser  # noqa: E402
from app.parsers.text_repair import repair_arabic  # noqa: E402

CORPUS = ROOT / "tests" / "corpus"
FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def parse(name: str, **kwargs):
    return PdfParser(**kwargs).parse((CORPUS / name).read_bytes(), name)


def arabic_reading_order_is_recovered() -> None:
    """Unit-level, because the failure is invisible in the output otherwise."""
    print("\n-- 1. Arabic is put back into reading order --")
    check(arabic_pdf.is_rtl("عقد تقديم خدمات"), "an Arabic line is recognised as RTL")
    check(not arabic_pdf.is_rtl("Bill of Quantities"), "an English line is not")
    check(
        not arabic_pdf.is_rtl("Invoice NRK-2026 total 4,250"),
        "nor a line of English and numbers",
    )
    check(
        arabic_pdf.is_rtl("قيمة العقد 2,680,000 درهم"),
        "and Arabic with numbers in it still is",
    )


def presentation_forms_and_controls_are_repaired() -> None:
    print("\n-- 2. display glyphs, tatweel, bidi marks and soft hyphens --")
    check(repair_arabic("ﻧﻴﺎﺑﺔ ﻣﺮﻭﺭ ﺃﺑﻮ ﻇﺒﻲ") == "نيابة مرور أبو ظبي", "presentation forms")
    check(repair_arabic("ﺗﻤــﺖ") == "تمت", "tatweel is removed")
    check(repair_arabic("‎GULF‏") == "GULF", "bidi marks are stripped")
    check(repair_arabic("CMN­2026­0817") == "CMN-2026-0817", "a soft hyphen inside a code becomes a hyphen")
    check(repair_arabic("نص عادي") == "نص عادي", "ordinary Arabic is left alone")


def headings_come_from_relative_size() -> None:
    print("\n-- 3. text set larger than the body becomes a heading --")
    doc = parse("structured.pdf")
    headings = [s.heading for s in doc.sections]

    check(doc.file_type == FileType.PDF, "typed as a PDF")
    check(len(doc.sections) >= 3, f"it did not collapse into one section ({len(doc.sections)})")
    check(
        any("القسم الأول" in h for h in headings),
        f"the headings read correctly ({headings})",
    )
    check(
        all("لوألا" not in h for h in headings),
        "and none of them came back reversed",
    )


def every_section_knows_its_page() -> None:
    print("\n-- 4. each section carries the page it is on --")
    doc = parse("structured.pdf")
    by_heading = {s.heading: s.locator for s in doc.sections}

    first = next((s for s in doc.sections if "القسم الأول" in s.heading), None)
    second = next((s for s in doc.sections if "القسم الثاني" in s.heading), None)
    third = next((s for s in doc.sections if "القسم الثالث" in s.heading), None)

    check(first is not None and first.locator == "صفحة 1", f"section one is on page 1 ({by_heading})")
    check(second is not None and second.locator == "صفحة 2", "section two on page 2")
    check(third is not None and third.locator == "صفحة 3", "section three on page 3")


def a_value_stays_on_the_page_it_was_printed_on() -> None:
    print("\n-- 5. a value is not attributed to the wrong page --")
    doc = parse("structured.pdf")
    second = next(s for s in doc.sections if "القسم الثاني" in s.heading)
    third = next(s for s in doc.sections if "القسم الثالث" in s.heading)

    check("CMN-2026-0817" in second.content, "the reference number is in section two")
    check("CMN-2026-0817" not in third.content, "and not in section three")
    check("268,000" in third.content, "the guarantee figure is in section three")


def a_document_with_no_headings_falls_back_to_pages() -> None:
    print("\n-- 6. with nothing set larger, pages become the sections --")
    doc = parse("flat.pdf")
    check(len(doc.sections) == 3, f"one section per page ({len(doc.sections)})")
    check([s.locator for s in doc.sections] == ["صفحة 1", "صفحة 2", "صفحة 3"], "each cites its own page")

    page2 = doc.sections[1]
    check("REF-002-VAL" in page2.content, "page 2 holds its own value")
    check("REF-001-VAL" not in page2.content, "and not page 1's")


def a_ruled_table_becomes_a_grid() -> None:
    print("\n-- 7. a table with ruled lines is extracted as a table --")
    doc = parse("ruled_table.pdf")
    section = doc.sections[0]
    table_blocks = [b for b in section.blocks if b.type == BlockType.TABLE]

    check(len(table_blocks) == 1, f"one table block ({len(table_blocks)})")
    check("| Item | Unit | Qty | Rate | Amount |" in section.content, "with its header row")
    check(
        table_blocks and table_blocks[0].location
        and table_blocks[0].location.locator() == "صفحة 1 — جدول 1",
        "cited by page and table number",
    )

    lines = [l for l in section.content.splitlines() if l.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    concrete = next(l for l in lines if "Concrete" in l)
    cells = [c.strip() for c in concrete.strip("|").split("|")]
    paired = dict(zip(header, cells))
    check(paired.get("Amount") == "71400", f"and each value under its column ({paired})")


def table_text_is_not_carried_twice() -> None:
    print("\n-- 8. a table's values appear once, not once loose and once in the grid --")
    doc = parse("ruled_table.pdf")
    content = doc.sections[0].content
    check(content.count("66432") == 1, f"the figure appears once ({content.count('66432')})")
    check(content.count("Reinforcement") == 1, "and so does the row label")


def an_unruled_table_keeps_its_text() -> None:
    """The honest common case: detection finds nothing, and nothing is lost."""
    print("\n-- 9. a table without ruled lines still keeps its values --")
    doc = parse("with_table.pdf")
    content = "\n".join(s.content for s in doc.sections)
    check("804000" in content, "the figures are still there")
    check("1206000" in content, "all of them")
    check("الدفعة" in content, "and the labels")


def a_scan_is_read_by_ocr_when_it_can_be() -> None:
    print("\n-- 10. a scanned document is read by OCR --")
    if not ocr.capability().available:
        check(False, f"OCR unavailable: {ocr.capability().reason}")
        return

    doc = parse("scanned.pdf")
    check(doc.metadata.get("ocr_pages") == 3, f"all three pages were OCR'd ({doc.metadata})")
    check(bool(doc.extraction_warning), "and the document is marked as OCR'd")
    content = "\n".join(s.content for s in doc.sections)
    check("القسم الأول" in content or "نطاق الأعمال" in content, "the Arabic came through")
    check([s.locator for s in doc.sections] == ["صفحة 1", "صفحة 2", "صفحة 3"], "pages are still cited")


def a_scan_is_refused_when_ocr_is_off() -> None:
    """The refusal is the feature: better no document than an empty one that looks fine."""
    print("\n-- 11. with OCR off, a scan is refused rather than indexed empty --")
    try:
        parse("scanned.pdf", enable_ocr=False)
        check(False, "it should not have parsed")
    except OcrRequiredError as exc:
        check(True, "OcrRequiredError, which maps to ocr_required")
        check("ممسوح ضوئيًا" in str(exc), "and the message says the file is a scan")


def only_the_image_pages_are_ocred() -> None:
    print("\n-- 12. a readable document with one scanned page keeps its good text --")
    if not ocr.capability().available:
        check(False, "OCR unavailable")
        return
    doc = parse("mixed_scan.pdf")
    check(doc.metadata.get("ocr_pages") == 1, f"exactly one page needed OCR ({doc.metadata})")
    content = "\n".join(s.content for s in doc.sections)
    check("نطاق الأعمال" in content, "the typed pages are still there")


def a_mostly_readable_document_is_not_refused() -> None:
    print("\n-- 13. one image page does not make a document a scan --")
    try:
        doc = parse("mixed_scan.pdf", enable_ocr=False)
        check(len(doc.sections) >= 1, "it parsed with the text it had")
        check("4,250" in "\n".join(s.content for s in doc.sections), "keeping the readable pages")
    except OcrRequiredError:
        check(False, "one image page out of three should not trigger the refusal")


def an_empty_pdf_is_refused() -> None:
    print("\n-- 14. a blank PDF is refused with a reason --")
    try:
        parse("empty.pdf", enable_ocr=False)
        check(False, "it should not have parsed")
    except (ExtractionError, OcrRequiredError):
        check(True, "refused rather than indexed as an empty document")


def a_corrupt_pdf_fails_as_a_parse_error() -> None:
    print("\n-- 15. a truncated PDF is a parsing failure --")
    try:
        parse("corrupt.pdf")
        check(False, "it should not have parsed")
    except ParsingError:
        check(True, "ParsingError, which maps to failed_parsing")
    except Exception as exc:  # noqa: BLE001
        check(False, f"raised {type(exc).__name__} instead")


def ocr_capability_is_reported_not_assumed() -> None:
    print("\n-- 16. what OCR can do here is checked, not assumed --")
    ability = ocr.capability()
    check(isinstance(ability.available, bool), "the capability is a definite answer")
    if ability.available:
        check(ability.has_arabic, "Arabic is installed")
        check(bool(ability.binary), f"and the engine is located ({ability.binary})")
    else:
        check(bool(ability.reason), f"or the reason is stated ({ability.reason})")


if __name__ == "__main__":
    if not (CORPUS / "structured.pdf").exists():
        print("corpus missing — run tests/corpus/build_pdf_corpus.py first", file=sys.stderr)
        raise SystemExit(2)

    arabic_reading_order_is_recovered()
    presentation_forms_and_controls_are_repaired()
    headings_come_from_relative_size()
    every_section_knows_its_page()
    a_value_stays_on_the_page_it_was_printed_on()
    a_document_with_no_headings_falls_back_to_pages()
    a_ruled_table_becomes_a_grid()
    table_text_is_not_carried_twice()
    an_unruled_table_keeps_its_text()
    a_scan_is_read_by_ocr_when_it_can_be()
    a_scan_is_refused_when_ocr_is_off()
    only_the_image_pages_are_ocred()
    a_mostly_readable_document_is_not_refused()
    an_empty_pdf_is_refused()
    a_corrupt_pdf_fails_as_a_parse_error()
    ocr_capability_is_reported_not_assumed()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("PDF parsing holds.")
