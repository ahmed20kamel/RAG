"""The arithmetic layer: what it calculates, what it refuses to, and what it says it did.

Two failures are being guarded against, and they pull in opposite directions.

One is the failure that prompted this: a number the model worked out, printed beside
quoted figures with a citation after it, indistinguishable from evidence. Every
calculated value here must arrive labelled as calculated and carrying its operands.

The other is the failure a calculator invites: filling in a gap. A missing figure must
stay missing, an unverifiable total must go unreported, and no relation may be asserted
that was not actually computed from values present in the document.

The last group is the reason the layer earns its place. Reading the cash flow statement
by eye, I concluded its operating section did not balance, and said so as a confirmed
finding. It balances. The run below is what settles it, and it settles it the same way
every time.

Run: python tests/test_arithmetic.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.arithmetic import (  # noqa: E402
    ArithmeticVerifier,
    parse_number,
    verify_grid,
)

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def context_of(*blocks: tuple[int, str]) -> str:
    """A context assembled the way the builder assembles one, for the verifier to read."""
    joined = []
    for citation, body in blocks:
        joined.append(f"[{citation}] الملف: doc.pdf | القسم: -\n{body}")
    return ("\n\n").join(joined)


def refs(*pairs: tuple[int, str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(citation=c, locator=l) for c, l in pairs]


# -- 1. reading numbers -----------------------------------------------------
def numbers_are_read_as_documents_write_them() -> None:
    print("\n-- 1. what counts as a number --")
    check(parse_number("150,000") == Decimal(150000), "thousands separators")
    check(parse_number("(633,487)") == Decimal(-633487), "brackets mean negative")
    check(parse_number("1,985.19") == Decimal("1985.19"), "decimals")
    check(parse_number("٣٥٠") == Decimal(350), "Arabic-Indic digits")
    check(parse_number("-") == Decimal(0), "a dash is a stated nil, which is zero")


def references_and_words_are_not_numbers() -> None:
    """The distinction the whole exercise turned on."""
    print("\n-- 2. a reference is not a value --")
    check(parse_number("6-A") is None, "a note reference with a suffix")
    check(parse_number("TOTAL ASSETS") is None, "a label")
    check(parse_number("AED") is None, "a currency heading")
    check(parse_number("") is None, "an empty cell")
    check(parse_number("12/03/2026") is None, "a date is not an amount")


# -- 3. totals --------------------------------------------------------------
def a_total_is_verified_against_its_parts() -> None:
    print("\n-- 3. a total that adds up is confirmed, with its operands --")
    grid = [
        ["", "Note", "2025", "2024"],
        ["Accounts Receivable", "6", "-", "-"],
        ["Due from Related Parties", "", "30,166", ""],
        ["Prepayments", "8", "150,000", "150,000"],
        ["TOTAL ASSETS", "", "180,166", "150,000"],
    ]
    found = verify_grid(grid, citation=3, locator="صفحة 5")
    check(bool(found), f"a relation was found ({len(found)})")

    total = next((d for d in found if d.label == "TOTAL ASSETS" and d.column == "2025"), None)
    check(total is not None, "the 2025 total is the one verified")
    if total:
        check(total.holds, "and it holds")
        check(total.computed == Decimal(180166), f"computed from the parts ({total.computed})")
        check(len(total.operands) >= 2, f"with its operands listed ({len(total.operands)})")
        check(total.citation == 3, "carrying the citation of the source it came from")
        check(total.locator == "صفحة 5", "and the locator")


def a_note_column_is_never_summed() -> None:
    """Note references sit in a numeric-looking column and must not be added up."""
    print("\n-- 4. the note column produces no totals --")
    grid = [
        ["", "Note", "AED"],
        ["Item one", "6", "100"],
        ["Item two", "7", "200"],
        ["Item three", "13", "300"],
        ["TOTAL", "", "600"],
    ]
    found = verify_grid(grid)
    check(any(d.column == "AED" for d in found), "the amount column is verified")
    check(
        not any(d.column == "Note" for d in found),
        f"and nothing is claimed about the note column ({[d.column for d in found]})",
    )


def an_unverifiable_total_is_not_reported() -> None:
    """Silence rather than a guess: any number misses some sum."""
    print("\n-- 5. a figure that matches nothing produces no claim --")
    grid = [
        ["", "AED"],
        ["Item one", "100"],
        ["Item two", "200"],
        ["Stated total", "999"],
    ]
    found = verify_grid(grid)
    check(not found, f"nothing is asserted about the 999 ({[d.label for d in found]})")


def nothing_is_invented_for_a_missing_cell() -> None:
    print("\n-- 6. a blank stays blank --")
    grid = [
        ["", "2025", "2024"],
        ["Item one", "100", ""],
        ["Item two", "200", ""],
        ["TOTAL", "300", ""],
    ]
    found = verify_grid(grid)
    check(any(d.column == "2025" and d.holds for d in found), "the filled column verifies")
    check(
        not any(d.column == "2024" for d in found),
        "and the empty one yields nothing at all",
    )


# -- 7. the case that corrected me ------------------------------------------
def the_cash_flow_operating_section_balances() -> None:
    """Recorded because I got this wrong by hand and reported it as a finding.

    Reading the flattened statement, I treated an orphaned line as an operand and
    concluded the section was out by 180,166. Summing the actual run shows it balances
    exactly. The value of doing this deterministically is that it does not depend on
    which lines a reader happens to group together.
    """
    print("\n-- 7. the statement that I said did not balance --")
    grid = [
        ["", "AED"],
        ["Profit for the year", "30,166"],
        ["Trade receivables", "(633,487)"],
        ["Other assets", "(150,000)"],
        ["Other payable", "603,321"],
        ["Net cash flow from operating activities (A)", "(150,000)"],
    ]
    found = verify_grid(grid)
    relation = next((d for d in found if "(A)" in d.label), None)
    check(relation is not None, f"the relation is found ({[d.label for d in found]})")
    if relation:
        check(relation.holds, "and the section does balance")
        check(
            relation.computed == Decimal(-150000),
            f"30,166 − 633,487 − 150,000 + 603,321 = {relation.computed}",
        )


# -- 8. how it is presented --------------------------------------------------
def a_calculated_value_says_it_is_calculated() -> None:
    print("\n-- 8. the wording cannot be mistaken for a quotation --")
    grid = [["", "AED"], ["A", "100"], ["B", "200"], ["Total", "300"]]
    found = verify_grid(grid, citation=2)
    check(bool(found), "a relation was found")
    if found:
        text = found[0].describe()
        check(text.startswith("محسوب:"), f"it opens by saying it was calculated ({text[:20]})")
        check("100" in text and "200" in text, "and names the values it used")


def the_block_is_empty_when_nothing_was_calculated() -> None:
    """The property that keeps every existing answer byte-identical."""
    print("\n-- 9. no tables, no block --")
    report = ArithmeticVerifier().verify(context_of((1, "A paragraph of ordinary prose.")))
    check(report.is_empty, "nothing derived from prose")
    check(report.render() == "", "and the prompt gains nothing at all")


def the_block_names_itself_as_calculated() -> None:
    print("\n-- 10. the block is labelled, and separate from the facts --")
    excerpt = "| Item | AED |\n| --- | --- |\n| A | 100 |\n| B | 200 |\n| Total | 300 |"
    report = ArithmeticVerifier().verify(context_of((4, excerpt)))
    check(not report.is_empty, f"the table was read ({len(report.derived)})")

    rendered = report.render()
    check("قيم محسوبة" in rendered, "the heading says these were calculated")
    check("وليست مقتبسة" in rendered, "and that they are not quotations")


def it_reads_tables_from_any_format() -> None:
    """One code path for all four formats, because the parsers all emit pipe rows."""
    print("\n-- 11. the same check works whatever produced the table --")
    excerpt = (
        "| البند | 2025 |\n| --- | --- |\n| بند أول | 40,000 |\n"
        "| بند ثانٍ | 60,000 |\n| الإجمالي | 100,000 |"
    )
    report = ArithmeticVerifier().verify(context_of((1, excerpt)), refs((1, "Sheet: ملخص")))
    check(not report.is_empty, "an Arabic table verifies the same way")
    if report.derived:
        check(report.derived[0].holds, "and the total holds")
        check(report.derived[0].locator == "Sheet: ملخص", "keeping its locator")


def a_broken_table_costs_only_itself() -> None:
    print("\n-- 12. malformed input does not take the answer down --")
    report = ArithmeticVerifier().verify(
        context_of((1, "| a |\n| --- |\n| 1 |"), (2, "not a table at all"), (3, ""))
    )
    check(report.is_empty, "nothing derived, nothing raised")


if __name__ == "__main__":
    numbers_are_read_as_documents_write_them()
    references_and_words_are_not_numbers()
    a_total_is_verified_against_its_parts()
    a_note_column_is_never_summed()
    an_unverifiable_total_is_not_reported()
    nothing_is_invented_for_a_missing_cell()
    the_cash_flow_operating_section_balances()
    a_calculated_value_says_it_is_calculated()
    the_block_is_empty_when_nothing_was_calculated()
    the_block_names_itself_as_calculated()
    it_reads_tables_from_any_format()
    a_broken_table_costs_only_itself()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Deterministic arithmetic holds.")
