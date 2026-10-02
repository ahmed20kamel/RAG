"""Tables from scanned PDFs: RAGFlow's cell-by-cell reading, kept only where it can be trusted.

RAGFlow rebuilds a scanned English table far better than Tesseract's loose lines, and
cannot read Arabic at all. This checks the three decisions that make the combination
safe: a table is converted faithfully, a table that is really misread Arabic is dropped,
a table whose numbers our own reading never saw is dropped — and nothing about the
helper can fail a document.

Offline and deterministic: RAGFlow is replaced by fixed responses or by an address
nothing listens on. The values are invented.

Run: python tests/test_table_assist.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.domain import FileType, ParsedDocument, Section  # noqa: E402
from app.services.table_assist import (  # noqa: E402
    RagflowTableAssist,
    agreement,
    html_table_to_markdown,
    misread_arabic,
    select,
)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


TABLE = (
    "<table><tr><th>No</th><th>Item</th><th>Unit</th><th>Qty</th><th>Rate</th><th>Amount</th></tr>"
    "<tr><td>7.10</td><td>Wall tiles, wet areas</td><td>M2</td><td>140.00</td><td>85.00</td><td>11,900.00</td></tr>"
    "<tr><td>7.20</td><td>Floor screed | 5cm</td><td>M2</td><td>320.00</td><td>45.00</td><td>14,400.00</td></tr>"
    "<tr><td colspan=5>Total</td><td>26,300.00</td></tr></table>"
)
# What an English-only reader makes of Arabic letters: a run of l, i and j.
MISREAD = "<table><tr><td>" + "lhll Jipleii ilgiipllaililllgllilpi llioojlbpaililg Lwliallelpyiliilg " * 2 + "</td><td>2026</td></tr></table>"
OUR_READING = "7.10 Wall tiles 140.00 85.00 11900 7.20 Floor 320.00 45.00 14400.00 Total 26300"


def scanned(text: str = OUR_READING) -> ParsedDocument:
    return ParsedDocument(
        title="BOQ", sections=[Section(heading="BOQ", level=1, content=text)], raw_text=text,
        language="en", metadata={"ocr_pages": 2, "pages": 2}, file_type=FileType.PDF,
    )


def main() -> int:
    print("\n=== 1. a table is converted cell by cell ===")
    md = html_table_to_markdown(TABLE)
    lines = md.splitlines()
    check(lines[0] == "| No | Item | Unit | Qty | Rate | Amount |", "header row kept", lines[0])
    check("| 7.10 | Wall tiles, wet areas | M2 | 140.00 | 85.00 | 11,900.00 |" in md,
          "each value stays beside its item")
    check("Floor screed / 5cm" in md, "a pipe inside a cell cannot break the row")
    check(all(line.count("|") == lines[0].count("|") for line in lines), "every row has the same width")

    print("\n=== 2. misread Arabic is recognised ===")
    check(misread_arabic(html_table_to_markdown(MISREAD)), "l/i/j-heavy Latin is treated as misread Arabic")
    check(not misread_arabic(md), "an English table is not")
    check(not misread_arabic("No Qty 12"), "too few letters to judge is not")

    print("\n=== 3. numbers confirm the table belongs to this file ===")
    count, share = agreement(md, OUR_READING)
    check(count >= 6 and share == 1.0, "every number found in our reading", f"{count} numbers, {share:.0%}")
    count, share = agreement(md, "a different document with 999,999 only")
    check(share == 0.0, "a table from elsewhere agrees with nothing")
    check(agreement("No numbers here", OUR_READING) == (0, 0.0), "a table without numbers has no agreement")

    print("\n=== 4. enrich: which tables are added ===")

    class Fixed(RagflowTableAssist):
        def __init__(self, chunks):
            super().__init__("http://unused", "key")
            self._fixed = chunks

        def tables(self, content, filename, our_text):
            # The real filter, over fixed RAGFlow chunks instead of a live call.
            return select(self._fixed, our_text, filename)

    helper = Fixed([
        {"content": TABLE, "positions": [[2, 0, 0, 0, 0]]},
        {"content": MISREAD, "positions": [[1, 0, 0, 0, 0]]},
        {"content": "plain text, not a table", "positions": [[1, 0, 0, 0, 0]]},
        {"content": TABLE.replace("140.00", "777.00").replace("11,900", "88,888").replace("85.00", "66.00")
                          .replace("320.00", "555.00").replace("45.00", "33.00").replace("14,400", "44,444")
                          .replace("26,300", "99,999").replace("7.10", "9.10").replace("7.20", "9.20"),
         "positions": [[3, 0, 0, 0, 0]]},
    ])
    doc = helper.enrich(scanned(), b"%PDF", "boq.pdf")
    added = [s for s in doc.sections if s.has_table]
    check(len(added) == 1, "only the trusted table is added", f"{len(added)} added")
    check(added and added[0].location.page == 2 and "صفحة 2" in added[0].heading, "it carries its page")
    check(doc.metadata.get("assisted_tables") == 1, "the count is recorded on the document")
    check(doc.sections[0].content == OUR_READING, "our own reading is left as it was")

    print("\n=== 5. never in the way ===")
    off = RagflowTableAssist("", "", enabled=True)
    check(not off.wanted(scanned()), "no URL or key: not used at all")
    digital = scanned()
    digital.metadata = {"pages": 2}
    check(not helper.wanted(digital), "a PDF with a text layer is not sent")
    markdown_doc = scanned()
    markdown_doc.file_type = FileType.MARKDOWN
    check(not helper.wanted(markdown_doc), "a non-PDF is not sent")
    down = RagflowTableAssist("http://127.0.0.1:9", "key", timeout=5)
    before = scanned()
    sections_before = len(before.sections)
    after = down.enrich(before, b"%PDF", "boq.pdf")
    check(len(after.sections) == sections_before and after.metadata.get("assisted_tables") == 0,
          "RAGFlow unreachable: the document is indexed exactly as before")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
