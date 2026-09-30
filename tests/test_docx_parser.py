"""The DOCX parser, against documents shaped like the ones this company writes.

The heading tree is what is really under test. Everything a citation says about a Word
document — which section, which table — comes from it, and it is also the part most
likely to be quietly wrong: a parser that finds no headings still produces text, still
indexes, still answers, and cites every passage to the top of the file.

So the two heading paths are tested separately, and the awkward middle case gets its own
test: a bold line that is emphasis rather than a heading, which is what a real report had
and what a naive rule breaks on.

Run: python tests/test_docx_parser.py   (build the corpus first)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.domain import BlockType, FileType  # noqa: E402
from app.exceptions import ExtractionError, ParsingError  # noqa: E402
from app.parsers.docx_parser import DocxParser  # noqa: E402

CORPUS = ROOT / "tests" / "corpus"
FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def parse(name: str):
    return DocxParser().parse((CORPUS / name).read_bytes(), name)


def heading_styles_build_a_tree() -> None:
    print("\n-- 1. heading styles become a nested tree --")
    doc = parse("styled_headings.docx")
    headings = [(s.level, s.heading) for s in doc.sections]

    check(doc.file_type == FileType.DOCX, "typed as a Word document")
    check(any(h == "شروط الدفع" for _, h in headings), "a level-2 heading is present")
    payment = next(s for s in doc.sections if s.heading == "شروط الدفع")
    check(payment.level == 2, f"its level is 2 (got {payment.level})")
    check(
        payment.path[:2] == ["عقد المقاولة", "شروط الدفع"],
        f"and its path runs through its parent ({payment.path})",
    )


def document_properties_are_kept() -> None:
    print("\n-- 2. what Word recorded about the document is carried over --")
    doc = parse("styled_headings.docx")
    check(doc.title == "عقد المقاولة", f"the title comes from the properties ({doc.title})")
    check(doc.metadata.get("author") == "الإدارة القانونية", "the author too")
    check(doc.metadata.get("category") == "عقود", "and the category")


def a_table_stays_under_its_heading() -> None:
    print("\n-- 3. a table belongs to the heading it was written under --")
    doc = parse("styled_headings.docx")
    payment = next(s for s in doc.sections if s.heading == "شروط الدفع")

    check("| الدفعة" in payment.content, "the table is rendered under شروط الدفع")
    check("804000" in payment.content, "with its figures")
    check(
        any(b.type == BlockType.TABLE for b in payment.blocks),
        "and it is recorded as a table block",
    )

    penalty = next(s for s in doc.sections if s.heading == "غرامة التأخير")
    check("804000" not in penalty.content, "and it did not drift into another section")


def a_table_keeps_its_columns() -> None:
    print("\n-- 4. a value stays under the column that names it --")
    doc = parse("styled_headings.docx")
    payment = next(s for s in doc.sections if s.heading == "شروط الدفع")
    lines = [l for l in payment.content.splitlines() if l.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    second = next(l for l in lines if "الدفعة الثانية" in l)
    cells = [c.strip() for c in second.strip("|").split("|")]

    check(len(header) == len(cells), "the row is as wide as the header")
    if len(header) == len(cells):
        paired = dict(zip(header, cells))
        check(paired.get("المبلغ") == "1206000", f"the amount is under المبلغ ({paired})")
        check(paired.get("النسبة") == "45%", "and the percentage under النسبة")


def bold_numbered_lines_are_found_when_no_style_declares_them() -> None:
    """The case every real document here turned out to be."""
    print("\n-- 5. a document with no heading styles still gets a tree --")
    doc = parse("bold_headings.docx")
    headings = [s.heading for s in doc.sections]

    check(len(doc.sections) >= 3, f"it did not collapse into one section ({len(doc.sections)})")
    check(any(h.startswith("1.") for h in headings), "the numbered headings were found")
    check(any(h.startswith("3.") for h in headings), "all of them")


def emphasis_is_not_mistaken_for_a_heading() -> None:
    print("\n-- 6. a bold line that is emphasis stays inside its section --")
    doc = parse("bold_headings.docx")
    headings = [s.heading for s in doc.sections]

    check(
        "Team = Supervisor + Workers" not in headings,
        f"it did not become a heading ({headings})",
    )
    first = next(s for s in doc.sections if s.heading.startswith("1."))
    check(
        "Team = Supervisor + Workers" in first.content,
        "it is still in the text where it was written",
    )
    check(
        "يتحول إلى نظام متكامل" in first.content,
        "and the sentence after it stayed in the same section",
    )


def a_table_between_paragraphs_keeps_its_place() -> None:
    """Catches the classic bug: reading all paragraphs, then all tables."""
    print("\n-- 7. a table in the middle does not move to the end --")
    doc = parse("interleaved.docx")
    second = next(s for s in doc.sections if s.heading == "القسم الثاني")
    third = next(s for s in doc.sections if s.heading == "القسم الثالث")

    check("PTW-9931" in second.content, "the table is in the section it was written in")
    check("PTW-9931" not in third.content, "not in the last one")
    check(
        second.content.index("PTW-9931") < second.content.index("نص يلي الجدول"),
        "and the paragraph after it still comes after it",
    )


def one_table_is_named_and_two_are_not() -> None:
    print("\n-- 8. a table is cited by number only when there is no doubt --")
    single = parse("styled_headings.docx")
    payment = next(s for s in single.sections if s.heading == "شروط الدفع")
    check(payment.locator.startswith("جدول"), f"one table is named ({payment.locator!r})")

    doubled = parse("tables_only.docx")
    annexes = next(s for s in doubled.sections if "الملاحق" in s.heading)
    check(
        sum(1 for b in annexes.blocks if b.type == BlockType.TABLE) == 2,
        "the section really does hold two tables",
    )
    check(
        annexes.locator == "",
        f"so no table is named, rather than the wrong one ({annexes.locator!r})",
    )


def both_languages_survive_together() -> None:
    print("\n-- 9. Arabic and English in one table --")
    doc = parse("mixed_languages.docx")
    whole = "\n".join(s.content for s in doc.sections)
    check("أعمال الحفر" in whole, "the Arabic cells are there")
    check("Excavation" in whole, "the English ones too")
    check("120 days" in whole, "and the values beside them")
    check(doc.language in ("mixed", "ar", "en"), f"the language is recorded ({doc.language})")


def a_long_document_keeps_its_chapters() -> None:
    print("\n-- 10. a long document is not flattened --")
    doc = parse("long_document.docx")
    check(len(doc.sections) >= 20, f"every chapter is a section ({len(doc.sections)})")
    chapter7 = next((s for s in doc.sections if s.heading == "الفصل 7"), None)
    check(chapter7 is not None, "chapter 7 is addressable")
    if chapter7:
        check("7000" in chapter7.content, "and its own value is inside it")
        check("8000" not in chapter7.content, "not the next chapter's")


def an_empty_document_is_refused() -> None:
    print("\n-- 11. an empty document is refused with a reason --")
    try:
        parse("empty.docx")
        check(False, "it should not have parsed")
    except ExtractionError:
        check(True, "ExtractionError, which maps to failed_extraction")


def a_corrupt_document_fails_as_a_parse_error() -> None:
    print("\n-- 12. a truncated file is a parsing failure --")
    try:
        parse("corrupt.docx")
        check(False, "it should not have parsed")
    except ParsingError:
        check(True, "ParsingError, which maps to failed_parsing")
    except Exception as exc:  # noqa: BLE001
        check(False, f"raised {type(exc).__name__} instead")


def a_workbook_renamed_docx_is_not_read_as_word() -> None:
    print("\n-- 13. a workbook renamed .docx is not parsed as Word --")
    try:
        parse("renamed_xlsx.docx")
        check(False, "it should not have parsed")
    except (ParsingError, ExtractionError):
        check(True, "refused rather than read as an empty document")
    except Exception as exc:  # noqa: BLE001
        check(False, f"raised {type(exc).__name__} instead")


if __name__ == "__main__":
    if not (CORPUS / "styled_headings.docx").exists():
        print("corpus missing — run tests/corpus/build_docx_corpus.py first", file=sys.stderr)
        raise SystemExit(2)

    heading_styles_build_a_tree()
    document_properties_are_kept()
    a_table_stays_under_its_heading()
    a_table_keeps_its_columns()
    bold_numbered_lines_are_found_when_no_style_declares_them()
    emphasis_is_not_mistaken_for_a_heading()
    a_table_between_paragraphs_keeps_its_place()
    one_table_is_named_and_two_are_not()
    both_languages_survive_together()
    a_long_document_keeps_its_chapters()
    an_empty_document_is_refused()
    a_corrupt_document_fails_as_a_parse_error()
    a_workbook_renamed_docx_is_not_read_as_word()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("DOCX parsing holds.")
