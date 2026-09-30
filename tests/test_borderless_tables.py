"""Tables made of alignment rather than rules, and the failure that motivated them.

The validation run produced an answer stating that receivables had risen to 6 AED. The
6 was a note reference. It became a figure because the page had no ruling lines, so the
table was never recognised as one and its columns were flattened into a stream of text
in which a reference and an amount are the same shape.

So the checks here are about relationships, not about text being present. Text was
always present. What was lost was which column a number stood in, and that is the only
thing that made the difference between a correct answer and a confident wrong one.

The last two cases guard the other direction. Prose must not become a table, because a
paragraph forced into columns would be a new way to produce nonsense; and nothing may
be computed, because a total this code worked out would be indistinguishable in the
output from one the document actually printed.

Run: python tests/test_borderless_tables.py   (build the corpus first)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import pymupdf  # noqa: E402

from app.core.domain import BlockType  # noqa: E402
from app.parsers import layout_tables as lt  # noqa: E402
from app.parsers.pdf_parser import PdfParser  # noqa: E402

CORPUS = ROOT / "tests" / "corpus"
FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def page_tables(name: str, index: int = 0) -> list[lt.Table]:
    document = pymupdf.open(CORPUS / name)
    try:
        return lt.extract(document[index])
    finally:
        document.close()


def as_map(table: lt.Table) -> dict[str, list[str]]:
    """Rows keyed by their label, so a check reads like the statement does."""
    return {row[0]: row[1:] for row in table.rows if row[0]}


# -- 1. the failure that started this --------------------------------------
def a_note_reference_is_not_an_amount() -> None:
    print("\n-- 1. a note number stays in the note column --")
    tables = page_tables("borderless_statement.pdf")
    check(len(tables) == 1, f"the statement is found as one table ({len(tables)})")
    if not tables:
        return

    table = tables[0]
    check(table.columns == 4, f"four columns: label, note, and two years ({table.columns})")

    rows = as_map(table)
    receivable = rows.get("Accounts Receivable")
    check(receivable is not None, f"the receivables row is addressable ({list(rows)[:4]})")
    if receivable:
        check(receivable[0] == "6", f"the 6 is in the note column ({receivable})")
        check(
            receivable[1] == "-" and receivable[2] == "-",
            "and both year columns hold the dash the document printed",
        )
        check(
            "6" not in receivable[1:],
            "the 6 appears nowhere among the amounts",
        )


def each_year_keeps_its_own_column() -> None:
    print("\n-- 2. the two year columns stay apart --")
    tables = page_tables("borderless_statement.pdf")
    if not tables:
        check(False, "no table")
        return
    table = tables[0]
    rows = as_map(table)

    header = next((r for r in table.rows if "Note" in r), None)
    check(header is not None, f"a header row names the columns ({header})")
    if header:
        check(header.index("2025") == 2 and header.index("2024") == 3, "2025 then 2024")

    prepayments = rows.get("Prepayments and Deposits")
    check(prepayments == ["8", "150,000", "150,000"], f"a row with both years ({prepayments})")

    total = rows.get("TOTAL ASSETS")
    check(total == ["", "180,166", "150,000"], f"and the total under the right years ({total})")


def a_blank_year_stays_blank() -> None:
    """The document leaves a cell empty; nothing may fill it in."""
    print("\n-- 3. an empty cell is reported as empty --")
    tables = page_tables("borderless_statement.pdf")
    if not tables:
        check(False, "no table")
        return
    rows = as_map(tables[0])

    due = rows.get("Due from Related Parties")
    check(due == ["", "30,166", ""], f"2025 has a figure, 2024 is blank ({due})")

    wip = rows.get("Work in Progress")
    check(wip == ["", "", ""], f"and a row with no figures at all stays empty ({wip})")


# -- 4. rows the layout split -----------------------------------------------
def figures_on_the_next_line_rejoin_their_label() -> None:
    print("\n-- 4. figures printed below their label belong to it --")
    tables = page_tables("offset_rows.pdf")
    check(len(tables) == 1, f"one table ({len(tables)})")
    if not tables:
        return
    rows = as_map(tables[0])

    ppe = rows.get("Property, Plant and Equipment")
    check(ppe == ["", "-", "-"], f"the dashes rejoined the item above them ({ppe})")


def a_note_printed_below_its_label_rejoins_it() -> None:
    print("\n-- 5. a note dropped under its label belongs to it --")
    tables = page_tables("offset_rows.pdf")
    if not tables:
        check(False, "no table")
        return
    rows = as_map(tables[0])

    payables = rows.get("Trade Payables")
    check(payables == ["11", "4,500", "3,200"], f"note 11 with its own row ({payables})")

    other = rows.get("Other Payables")
    check(other == ["", "1,100", "900"], f"and the next row did not take it ({other})")


def an_unlabelled_subtotal_stays_its_own_row() -> None:
    """The mirror of the merge: a subtotal must not be absorbed by the item above it."""
    print("\n-- 6. an unlabelled subtotal is not swallowed --")
    tables = page_tables("offset_rows.pdf")
    if not tables:
        check(False, "no table")
        return
    table = tables[0]

    other = next((r for r in table.rows if r[0] == "Other Payables"), None)
    check(other == ["Other Payables", "", "1,100", "900"], f"the item keeps its figures ({other})")

    subtotal = [r for r in table.rows if not r[0] and r[2] == "5,600"]
    check(len(subtotal) == 1, f"the subtotal is a row of its own ({subtotal})")


# -- 7. what must not happen ------------------------------------------------
def prose_does_not_become_a_table() -> None:
    print("\n-- 7. indented prose is left as prose --")
    tables = page_tables("prose_with_gaps.pdf")
    check(not tables, f"no table was invented ({len(tables)})")


def two_tables_stay_two() -> None:
    print("\n-- 8. two tables on one page keep their own shapes --")
    tables = page_tables("two_tables.pdf")
    check(len(tables) == 2, f"two tables ({len(tables)})")
    if len(tables) == 2:
        check(
            sorted(t.columns for t in tables) == [2, 3],
            f"with two and three columns ({[t.columns for t in tables]})",
        )
        headcount = next((t for t in tables if t.columns == 3), None)
        if headcount:
            rows = as_map(headcount)
            check(rows.get("Site") == ["24", "19"], f"and their own values ({rows.get('Site')})")


def nothing_is_calculated() -> None:
    """Every cell must be a word that was on the page — never a value worked out here.

    A computed subtotal would arrive in the answer looking exactly like a printed one,
    and there would be no way for a reader to tell which they were being shown.
    """
    print("\n-- 9. every cell came off the page, none was computed --")
    document = pymupdf.open(CORPUS / "borderless_statement.pdf")
    try:
        printed = {w[4].strip() for w in document[0].get_text("words") if w[4].strip()}
        tables = lt.extract(document[0])
    finally:
        document.close()

    produced = {
        cell.strip()
        for table in tables
        for row in table.rows
        for cell in row
        if cell.strip()
    }
    invented = {
        cell for cell in produced
        if cell not in printed and not all(part in printed for part in cell.split())
    }
    check(not invented, f"no cell holds text the page does not ({sorted(invented)[:5]})")

    # 180,166 is on the page; 210,166 would be a plausible sum that is not.
    check("180,166" in produced, "the printed total is reported")
    check(
        not any(c not in printed and c.replace(",", "").isdigit() for c in produced),
        "and no figure appears that was not printed",
    )


def a_right_to_left_cell_reads_in_order() -> None:
    """Unit-level, because no PDF this project can generate holds valid Arabic tokens.

    Both available ways of writing Arabic into a PDF produced files whose own words
    were wrong — adjacent cells merged into a single token spanning two columns, with
    the letters reversed inside it. A fixture like that tests the generator. Real
    Arabic PDFs tokenise correctly, which the corpus suite covers; what is checked here
    is the ordering decision this module makes once it has the words.
    """
    print("\n-- 10. an Arabic cell is joined right to left --")
    check(
        lt._join_cell([(10.0, "المدينة"), (40.0, "الذمم")]) == "الذمم المدينة",
        "the rightmost word comes first",
    )
    check(
        lt._join_cell([(10.0, "Trade"), (40.0, "Payables")]) == "Trade Payables",
        "and an English cell is left alone",
    )
    check(lt._join_cell([(10.0, "150,000")]) == "150,000", "a figure is untouched")


def figures_are_told_apart_from_words() -> None:
    print("\n-- 11. what counts as a figure --")
    for text in ("150,000", "(633,487)", "30,166", "6", "4.9", "45%", "١٥٠"):
        check(lt.is_numeric(text), f"{text!r} reads as a figure")
    for text in ("TOTAL ASSETS", "Note", "AED", "6-A", "-"):
        check(not lt.is_numeric(text), f"{text!r} does not")


# -- 12. through the parser --------------------------------------------------
def the_parser_emits_it_as_a_table() -> None:
    print("\n-- 12. the parser turns it into a table block with a page locator --")
    parsed = PdfParser().parse((CORPUS / "borderless_statement.pdf").read_bytes(), "b.pdf")
    blocks = [b for s in parsed.sections for b in s.blocks if b.type == BlockType.TABLE]

    check(len(blocks) == 1, f"one table block ({len(blocks)})")
    if blocks:
        block = blocks[0]
        check(
            block.location is not None and block.location.locator() == "صفحة 1 — جدول 1",
            f"cited by page and table ({block.location.locator() if block.location else None})",
        )
        check(
            "| Accounts Receivable | 6 | - | - |" in block.text,
            "and rendered with the note in its own column",
        )
        check(block.structured_data is not None, "with the grid kept alongside the text")


def the_rendered_text_keeps_the_columns() -> None:
    print("\n-- 13. the text the model reads keeps the relationships --")
    parsed = PdfParser().parse((CORPUS / "borderless_statement.pdf").read_bytes(), "b.pdf")
    content = "\n".join(s.content for s in parsed.sections)

    lines = [l for l in content.splitlines() if l.startswith("|")]
    check(bool(lines), "the section holds pipe rows")

    row = next((l for l in lines if "Accounts Receivable" in l), "")
    cells = [c.strip() for c in row.strip("|").split("|")]
    check(len(cells) == 4, f"the row has four cells ({cells})")
    check(cells[1] == "6", "with the note in the second")

    # Counting occurrences of the figure would be the wrong test: 180,166 is printed
    # twice in the statement, as total assets and as total equity, because a balance
    # sheet balances. What must not happen is the same figure appearing both inside the
    # grid and again as a loose line that has lost its column.
    loose = [
        l for l in content.splitlines()
        if not l.startswith("|") and "180,166" in l
    ]
    check(not loose, f"no figure survives outside the table it belongs to ({loose})")


if __name__ == "__main__":
    if not (CORPUS / "borderless_statement.pdf").exists():
        print("corpus missing — run tests/corpus/build_borderless_corpus.py", file=sys.stderr)
        raise SystemExit(2)

    a_note_reference_is_not_an_amount()
    each_year_keeps_its_own_column()
    a_blank_year_stays_blank()
    figures_on_the_next_line_rejoin_their_label()
    a_note_printed_below_its_label_rejoins_it()
    an_unlabelled_subtotal_stays_its_own_row()
    prose_does_not_become_a_table()
    two_tables_stay_two()
    nothing_is_calculated()
    a_right_to_left_cell_reads_in_order()
    figures_are_told_apart_from_words()
    the_parser_emits_it_as_a_table()
    the_rendered_text_keeps_the_columns()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Borderless table extraction holds.")
