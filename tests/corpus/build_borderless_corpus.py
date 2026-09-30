"""Borderless tables built to the shapes that broke the parser.

A financial statement laid out with alignment rather than rules is what exposed the
flattening, and these reproduce every property of it that mattered — without using the
document itself, which holds a real company's figures.

Each fixture isolates one thing that went wrong:

* `borderless_statement.pdf` — a note column between labels and two year columns. This
  is the one where a note reference, 6, was read as an amount of 6 AED.
* `offset_rows.pdf` — an item whose figures sit on the line below its name, and a note
  dropped half a line under its label. Both were attributed to the wrong row.
* `prose_with_gaps.pdf` — indented prose with wide gaps, which must NOT become a table.
* `two_tables.pdf` — two tables on one page with different column counts.
Right-to-left column handling is NOT tested through a generated PDF. Writing Arabic
with either available method produced a file whose own tokens were wrong — adjacent
cells merged into one word spanning both columns, letters reversed inside it — so
the fixture would have been testing the generator. Real Arabic PDFs tokenise
correctly, as the corpus suite shows, and the reading-order logic is covered
directly by a unit test instead.

Drawn with `insert_text` at explicit coordinates, so the geometry under test is the
geometry written here — no renderer decides it.

    python tests/corpus/build_borderless_corpus.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")

FONT_DIR = next(
    (d for d in ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts")
     if Path(d).exists()),
    "",
)
FONT_FILE = next(
    (f for f in ("arial.ttf", "tahoma.ttf", "DejaVuSans.ttf") if FONT_DIR and (Path(FONT_DIR) / f).exists()),
    "",
)
CSS = f"@font-face{{font-family:ar;src:url({FONT_FILE});}} *{{font-family:ar;}}"


def _write(page, cells: list[tuple[float, float, str]], size: int = 10) -> None:
    """Words at exact positions. Right-aligned figures are placed by their right edge."""
    for x, y, text in cells:
        page.insert_text((x, y), text, fontsize=size, fontname="helv")


def borderless_statement() -> pymupdf.Document:
    """Labels, a note column, and two year columns — no rules anywhere."""
    document = pymupdf.open()
    page = document.new_page()
    LABEL, NOTE, Y25, Y24 = 70.0, 330.0, 420.0, 500.0

    _write(page, [(LABEL, 70, "STATEMENT OF FINANCIAL POSITION")], size=13)
    rows = [
        ("", "Note", "2025", "2024"),
        ("CURRENT ASSETS", "", "", ""),
        ("Accounts Receivable", "6", "-", "-"),
        ("Due from Related Parties", "", "30,166", ""),
        ("Work in Progress", "", "", ""),
        ("Cash and cash equivalents", "7", "-", "-"),
        ("Prepayments and Deposits", "8", "150,000", "150,000"),
        ("TOTAL ASSETS", "", "180,166", "150,000"),
        ("Share capital", "9", "150,000", "150,000"),
        ("Retained Earnings", "", "30,166", "-"),
        ("TOTAL EQUITY", "", "180,166", "150,000"),
    ]
    y = 110.0
    for label, note, v25, v24 in rows:
        cells = []
        if label:
            cells.append((LABEL, y, label))
        if note:
            cells.append((NOTE, y, note))
        if v25:
            cells.append((Y25, y, v25))
        if v24:
            cells.append((Y24, y, v24))
        _write(page, cells)
        y += 18
    return document


def offset_rows() -> pymupdf.Document:
    """Figures a line below their label, and a note a line below its label."""
    document = pymupdf.open()
    page = document.new_page()
    LABEL, NOTE, Y25, Y24 = 70.0, 330.0, 420.0, 500.0

    _write(page, [(LABEL, 70, "SCHEDULE OF BALANCES")], size=13)
    _write(page, [(NOTE, 100, "Note"), (Y25, 100, "2025"), (Y24, 100, "2024")])

    # A long name on one line; its figures on the next.
    _write(page, [(LABEL, 130, "Property, Plant and Equipment")])
    _write(page, [(Y25, 148, "-"), (Y24, 148, "-")])

    # Label and figures together, then a note dropped below.
    _write(page, [(LABEL, 170, "Trade Payables"), (Y25, 170, "4,500"), (Y24, 170, "3,200")])
    _write(page, [(NOTE, 182, "11")])

    _write(page, [(LABEL, 204, "Other Payables"), (Y25, 204, "1,100"), (Y24, 204, "900")])

    # An unlabelled subtotal — it must stay its own row, not join the line above.
    _write(page, [(Y25, 226, "5,600"), (Y24, 226, "4,100")])

    _write(page, [(LABEL, 250, "TOTAL LIABILITIES"), (Y25, 250, "5,600"), (Y24, 250, "4,100")])
    return document


def prose_with_gaps() -> pymupdf.Document:
    """Indented prose. Nothing here is a table, and nothing may be read as one."""
    document = pymupdf.open()
    page = document.new_page()
    lines = [
        "The Company was incorporated in Abu Dhabi under a commercial",
        "        licence issued by the Department of Economic Development.",
        "The principal activities comprise interior design implementation",
        "        works and the maintenance of buildings and related services.",
        "These financial statements are presented in UAE Dirhams, which is",
        "        the functional and presentation currency of the Company.",
        "The preparation of financial statements requires management to",
        "        make judgements, estimates and assumptions that affect the",
        "        reported amounts of assets and liabilities at the date of",
        "        the statement of financial position.",
    ]
    y = 90.0
    for line in lines:
        _write(page, [(70.0, y, line)])
        y += 18
    return document


def two_tables() -> pymupdf.Document:
    """Two tables on one page, different shapes, separated by a gap."""
    document = pymupdf.open()
    page = document.new_page()

    _write(page, [(70, 70, "REVENUE BY SEGMENT")], size=12)
    for index, (label, value) in enumerate(
        [("Segment", "2025"), ("Interior works", "420,000"), ("Maintenance", "213,487"), ("Total", "633,487")]
    ):
        y = 95 + index * 18
        _write(page, [(70.0, y, label), (430.0, y, value)])

    _write(page, [(70, 260, "HEADCOUNT")], size=12)
    for index, (a, b, c) in enumerate(
        [("Department", "2025", "2024"), ("Site", "24", "19"), ("Office", "6", "5"), ("Total", "30", "24")]
    ):
        y = 285 + index * 18
        _write(page, [(70.0, y, a), (380.0, y, b), (470.0, y, c)])
    return document


BUILDERS = {
    "borderless_statement.pdf": borderless_statement,
    "offset_rows.pdf": offset_rows,
    "prose_with_gaps.pdf": prose_with_gaps,
    "two_tables.pdf": two_tables,
}


def build() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    for name, builder in BUILDERS.items():
        document = builder()
        document.save(HERE / name)
        document.close()
        print(f"  {name:28} {(HERE / name).stat().st_size:>8,} bytes")


if __name__ == "__main__":
    print("building the borderless-table corpus\n")
    build()
    print(f"\nwritten to {HERE}")
