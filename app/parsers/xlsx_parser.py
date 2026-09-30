"""Workbooks, read so that an answer can cite the sheet and rows it came from.

A spreadsheet is the format where flattening does the most damage. "5,250" means nothing
without the column it sits under and the row it sits in, and a naive extraction that
concatenates cells produces text that retrieves well and answers wrongly. So the shape is
preserved and carried into the citation: every chunk knows its sheet and its row range,
and a reader can open the file at that range and see the same figure.

What the design is actually built around, learned from the real workbooks in this
company rather than from the format specification:

* The header is not the first row. Real sheets open with a title and a project line, and
  the row naming the columns is two or three rows down. It is searched for.
* `#REF!` is everywhere. A formula whose target is gone leaves an error string that
  arrives looking like data, so errors are dropped rather than indexed.
* Arabic arrives as presentation forms in some files, exactly as it does from a PDF.
* One sheet holds several tables, separated by blank rows.
* Two-column sheets are label/value forms, not tables — and that shape is already what
  the entity extractor reads facts out of.

Nothing here knows what a BOQ or a VAT column is. It reads structure, not subject.
"""

from __future__ import annotations

import logging
from io import BytesIO

from app.core.domain import (
    Block,
    BlockType,
    FileType,
    ParsedDocument,
    Section,
    SourceLocation,
)
from app.exceptions import ExtractionError, ParsingError
from app.parsers import tables
from app.parsers.base import DocumentParser
from app.parsers.text_repair import cell_value
from app.core.text import detect_language

logger = logging.getLogger(__name__)

#: A sheet wider than this is almost always a layout canvas rather than a table. The
#: columns beyond it are read anyway — this only caps how far the scan looks for one.
MAX_SCAN_COLUMNS = 64

#: How far down to look for the row that names the columns.
HEADER_SEARCH_ROWS = 12

#: Consecutive blank rows that end one table and begin another.
BLANK_ROWS_SEPARATE = 2

#: Below this, a run of rows is a stray fragment rather than a table of its own.
MIN_REGION_ROWS = 1


class XlsxParser(DocumentParser):
    name = "xlsx"
    supported_extensions = (".xlsx", ".xlsm")

    def __init__(self, max_rows_per_block: int = tables.MAX_ROWS_PER_BLOCK) -> None:
        self.max_rows_per_block = max_rows_per_block

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ParsingError("مكتبة قراءة Excel غير مثبتة على الخادم.") from exc

        try:
            # read_only streams the sheet instead of building every cell in memory, and
            # data_only takes the value Excel last calculated rather than the formula.
            workbook = openpyxl.load_workbook(
                BytesIO(content), read_only=True, data_only=True
            )
        except Exception as exc:  # noqa: BLE001 - openpyxl raises a wide range here
            raise ParsingError(
                f"تعذر فتح المصنف '{filename}'. قد يكون تالفًا أو محميًا بكلمة مرور."
            ) from exc

        sections: list[Section] = []
        text_parts: list[str] = []
        try:
            for sheet in workbook.worksheets:
                produced = self._read_sheet(sheet)
                sections.extend(produced)
                text_parts.extend(s.content for s in produced)
        finally:
            workbook.close()

        if not sections:
            raise ExtractionError(
                f"المصنف '{filename}' لا يحتوي على بيانات قابلة للفهرسة "
                f"(كل الأوراق فارغة أو تحتوي أخطاء صيغ فقط)."
            )

        raw_text = "\n\n".join(text_parts)
        return ParsedDocument(
            title=filename.rsplit(".", 1)[0],
            sections=sections,
            raw_text=raw_text,
            language=detect_language(raw_text),
            file_type=FileType.XLSX,
            metadata={"sheets": len(workbook.worksheets)},
        )

    # -- one sheet --------------------------------------------------------
    def _read_sheet(self, sheet) -> list[Section]:
        """A sheet as one section per table found in it.

        Row numbers are kept as Excel reports them — 1-based, counting blank rows — so a
        citation to "صفوف 12–18" points at the rows a person sees when they open the
        file. Renumbering after dropping blanks would produce citations that are
        internally consistent and externally wrong.
        """
        rows = self._rows_with_numbers(sheet)
        if not rows:
            return []

        sections: list[Section] = []
        for index, region in enumerate(self._regions(rows)):
            section = self._region_to_section(sheet.title, region, index)
            if section is not None:
                sections.append(section)
        return sections

    @staticmethod
    def _rows_with_numbers(sheet) -> list[tuple[int, list[str]]]:
        """Every non-empty row, paired with its Excel row number."""
        collected: list[tuple[int, list[str]]] = []
        for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = [cell_value(v) for v in values[:MAX_SCAN_COLUMNS]]
            if any(cells):
                collected.append((number, cells))
        return collected

    @staticmethod
    def _regions(rows: list[tuple[int, list[str]]]) -> list[list[tuple[int, list[str]]]]:
        """Runs of rows split where the sheet left a gap.

        The gap is measured in Excel row numbers rather than by counting blanks, because
        blank rows were already dropped. Two or more missing numbers in a row means the
        author put space there, and space in a spreadsheet almost always means "a
        different table starts here".
        """
        if not rows:
            return []
        regions: list[list[tuple[int, list[str]]]] = [[rows[0]]]
        for previous, current in zip(rows, rows[1:]):
            if current[0] - previous[0] > BLANK_ROWS_SEPARATE:
                regions.append([current])
            else:
                regions[-1].append(current)
        return [r for r in regions if len(r) >= MIN_REGION_ROWS]

    def _region_to_section(
        self, sheet_name: str, region: list[tuple[int, list[str]]], index: int
    ) -> Section | None:
        numbers = [n for n, _ in region]
        grid = tables.normalise_grid([cells for _, cells in region])
        if not grid:
            return None

        header, body, header_offset = self._split_header(grid)
        first_row = numbers[0]
        last_row = numbers[-1]

        heading = sheet_name if index == 0 else f"{sheet_name} ({index + 1})"
        blocks: list[Block] = []
        rendered: list[str] = []

        body_numbers = numbers[header_offset:]
        for start, group in tables.split_rows(body, self.max_rows_per_block):
            # The header is repeated on every block. A chunk that begins mid-table would
            # otherwise carry rows whose columns are named nowhere inside it, and a
            # figure with no column name is not an answer to anything.
            text = tables.render(group, header=header)
            group_first = body_numbers[start] if start < len(body_numbers) else first_row
            group_last = (
                body_numbers[min(start + len(group), len(body_numbers)) - 1]
                if body_numbers
                else last_row
            )
            location = SourceLocation(
                sheet=sheet_name, row_start=group_first, row_end=group_last
            )
            blocks.append(
                Block(
                    type=BlockType.SHEET_RANGE,
                    text=text,
                    structured_data=([header] if header else []) + group,
                    location=location,
                    order=len(blocks),
                )
            )
            rendered.append(text)

        if not rendered:
            return None

        return Section(
            heading=heading,
            level=1,
            content="\n\n".join(rendered),
            path=[heading],
            blocks=blocks,
            # The section spans the whole region; each block narrows it further, and the
            # chunker takes whichever one it is actually carrying.
            location=SourceLocation(
                sheet=sheet_name, row_start=first_row, row_end=last_row
            ),
        )

    @staticmethod
    def _split_header(
        grid: list[list[str]],
    ) -> tuple[list[str] | None, list[list[str]], int]:
        """The row that names the columns, the rows beneath it, and where it was found.

        Searched rather than assumed. Every real workbook here opens with a title line
        and a project line before the columns are named, and taking row one would put a
        document title where the column names belong — which then repeats at the top of
        every chunk of that table.
        """
        limit = min(HEADER_SEARCH_ROWS, len(grid))
        for offset in range(limit):
            candidate = grid[offset]
            below = grid[offset + 1 :]
            if not below:
                break
            # A title row is one filled cell in a wide row; a header names most columns.
            filled = sum(1 for cell in candidate if cell)
            if filled < 2 or filled < len(candidate) / 2:
                continue
            if tables.looks_like_header(candidate, below):
                return candidate, below, offset + 1

        # No row named the columns. Rendering without a header is correct here — an
        # invented one would put made-up names above real values.
        return None, grid, 0
