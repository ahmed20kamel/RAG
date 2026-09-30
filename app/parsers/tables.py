"""Turning a grid of cells into something the existing pipeline already understands.

Every parser that finds a table hands it here, and what comes back is a Markdown pipe
table. That choice is the whole reason multi-format ingestion does not require rewriting
the pipeline: the chunker treats a run of pipe rows as one atomic block and never cuts a
table in half, the structure analyzer sets `has_table` from the same shape, and the
entity extractor reads `| label | value |` rows into the deterministic fact sheet. A
spreadsheet row becomes a citable fact through code that was written years before any
spreadsheet reached it.

The header repetition below is the other half of that. A chunker splits a long table
across chunks, and a chunk that begins in the middle of one carries rows whose columns
are no longer named anywhere in it. "5,250" without "ضريبة القيمة المضافة" above it is
not an answer to anything, so wide or long tables repeat their header.
"""

from __future__ import annotations

import re

from app.parsers.text_repair import cell_value

#: A pipe inside a cell would end the cell early and shift every value after it into the
#: wrong column, so it is escaped rather than dropped.
_PIPE = re.compile(r"\|")

#: Beyond this many rows, a table is split into several blocks, each carrying the header.
MAX_ROWS_PER_BLOCK = 40

#: A cell long enough to bury the row it belongs to. Truncated in the rendered text only;
#: `structured_data` keeps every character.
MAX_CELL_CHARS = 300


def clean_cell(value: object) -> str:
    """One cell as text: no pipes, no newlines, no runs of whitespace.

    A newline inside a cell would split one row into two malformed ones, which is worse
    than losing the line break, so it becomes a space.
    """
    # Spreadsheet errors become empty and contextual Arabic glyphs become letters,
    # before anything else looks at the text.
    text = cell_value(value)
    if not text:
        return ""
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = _PIPE.sub("\\|", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CELL_CHARS:
        text = text[: MAX_CELL_CHARS - 1] + "…"
    return text


def normalise_grid(rows: list[list[object]]) -> list[list[str]]:
    """A ragged grid squared off, with fully empty rows and columns removed.

    Real spreadsheets are full of blank spacer rows and trailing empty columns that the
    file format reports as real cells. Keeping them would put empty columns in the
    rendered header and make every row wider than it is.
    """
    cleaned = [[clean_cell(cell) for cell in row] for row in rows]
    cleaned = [row for row in cleaned if any(cell for cell in row)]
    if not cleaned:
        return []

    width = max(len(row) for row in cleaned)
    squared = [row + [""] * (width - len(row)) for row in cleaned]

    keep = [i for i in range(width) if any(row[i] for row in squared)]
    return [[row[i] for i in keep] for row in squared]


def looks_like_header(row: list[str], body: list[list[str]]) -> bool:
    """Whether the first row names the columns rather than holding data.

    Decided by contrast, not by formatting: a header is text where the rows beneath it
    are mostly numbers. When the column is text all the way down there is nothing to
    contrast, so the row is treated as a header only if it is completely filled — which
    is the one thing a spacer row never is.
    """
    if not row or not any(row):
        return False
    if not body:
        return all(cell.strip() for cell in row)

    here = _numeric_ratio(row)
    below = max(
        (_numeric_ratio(r) for r in body[:10] if any(r)), default=0.0
    )

    if below > 0:
        # Proportions, not counts. Requiring a header to hold no number at all sounds
        # strict in the right direction, but it fails on real sheets: a BOQ header here
        # carries "ADD | 0.1 | for all cocrete" typed into the cells to its right, and
        # one stray figure among nine column names disqualified the whole row. What
        # separates a header from data is that it is markedly *less* numeric than what
        # follows it, which survives a note in the margin.
        return here <= HEADER_MAX_NUMERIC and (below - here) >= HEADER_MIN_CONTRAST
    return all(cell.strip() for cell in row)


#: A row this numeric is data, whatever sits beneath it.
HEADER_MAX_NUMERIC = 0.34

#: How much less numeric than the body a row must be before it reads as naming columns.
HEADER_MIN_CONTRAST = 0.25


def _numeric_ratio(row: list[str]) -> float:
    """The share of a row's filled cells that hold numbers."""
    filled = [cell for cell in row if cell]
    if not filled:
        return 0.0
    return sum(1 for cell in filled if _is_numeric(cell)) / len(filled)


def _is_numeric(cell: str) -> bool:
    stripped = cell.replace(",", "").replace("٬", "").replace("%", "").strip()
    stripped = re.sub(r"[^\d.\-/]", "", stripped)
    if not stripped:
        return False
    try:
        float(stripped)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[\d\-/.]{4,}", stripped))


def render(
    rows: list[list[str]], *, header: list[str] | None = None, caption: str = ""
) -> str:
    """A Markdown pipe table, optionally under a caption line."""
    if not rows and not header:
        return ""

    # Cleaned here as well as in `normalise_grid`, because this is the last place a stray
    # pipe can still do damage. An unescaped pipe does not produce a visibly broken
    # table — it produces a well-formed one with every value in the wrong column, which
    # is then cited as confidently as a correct one. Cleaning twice is cheap; a parser
    # that forgets to clean once is not.
    lines: list[str] = []
    if caption:
        lines.append(f"**{clean_cell(caption)}**")
        lines.append("")

    if header:
        safe_header = [clean_cell(cell) for cell in header]
        lines.append("| " + " | ".join(safe_header) + " |")
        lines.append("| " + " | ".join("---" for _ in safe_header) + " |")
    for row in rows:
        lines.append("| " + " | ".join(clean_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def split_rows(
    rows: list[list[str]], limit: int = MAX_ROWS_PER_BLOCK
) -> list[tuple[int, list[list[str]]]]:
    """Body rows in groups, each paired with the index of its first row.

    The index is returned rather than recomputed by the caller because it becomes the
    row number in the citation, and a citation that points at the wrong rows is worse
    than one that points nowhere.
    """
    if not rows:
        return []
    return [(start, rows[start : start + limit]) for start in range(0, len(rows), limit)]
