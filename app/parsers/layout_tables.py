"""Tables that were drawn with whitespace instead of lines, recovered from geometry.

PyMuPDF finds a table by its ruling lines. Financial statements rarely have any: the
columns are made by alignment, and to the file they are just words at coordinates. Those
pages used to come out as a single stream of text, and that is not a cosmetic loss —

    Accounts Recievable    6    -    -

flattens to four separate lines, and the 6, which is a note reference, becomes
indistinguishable from an amount. A reader of that text can conclude receivables were
6 AED. One did.

The recovery is positional. Words are grouped into rows by their baseline and into
columns by the vertical corridors of whitespace that run down the page, which is the
same thing the eye uses. Nothing here knows what a financial statement is: it finds
columns, not meanings, so a price list or a schedule of quantities recovers the same way.

The note-versus-amount problem is not solved by a rule about notes. It is solved by
getting the columns right, after which the 6 sits under a column whose own header says
"Note" — the document was always saying so, and flattening was what silenced it.

Nothing is computed. No total is summed, no blank is filled, no value is inferred from
its neighbours. Every cell that comes out of here was a word on the page.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.parsers.arabic_pdf import is_rtl
from app.parsers.text_repair import repair_arabic

logger = logging.getLogger(__name__)

#: Words whose baselines are within this many points are on the same visual row.
ROW_TOLERANCE = 3.0

#: A vertical corridor of whitespace this wide separates one column from the next.
#: Below it, the gap is word spacing; a financial statement's columns sit tens of
#: points apart, and so does any table laid out by alignment rather than by rule.
MIN_COLUMN_GAP = 12.0

#: Columns are found from the corridors that survive across the page, so a single
#: short line cannot invent one. This is the share of rows a corridor must be clear in.
CORRIDOR_CLEAR_SHARE = 0.9

#: The first, permissive pass. Loose enough that a centred letterhead cannot erase a
#: corridor on its own, strict enough not to invent one from a single wide gap.
PERMISSIVE_CLEAR_SHARE = 0.55

#: How much of a block must agree with the layout before it is treated as one table.
MIN_CONFORMING_SHARE = 0.5

#: What makes a run of rows a table rather than prose. All of these must hold, and they
#: are deliberately strict: turning a paragraph into a two-column table would be a worse
#: outcome than leaving a table as text.
MIN_TABLE_ROWS = 3
MIN_TABLE_COLUMNS = 2
MIN_NUMERIC_SHARE = 0.3

#: A row carrying no label merges upward only within this multiple of the row spacing.
MAX_CONTINUATION_GAP = 2.0

#: Text that reads as a figure: digits, with the separators and signs a statement uses.
#: Brackets are how accounts write a negative, and a lone dash is how they write nil.
NUMERIC = re.compile(r"^[(\[]?-?[\d٠-٩][\d٠-٩,،.\s]*%?[)\]]?$")
NIL = re.compile(r"^[-–—]$")


@dataclass
class Row:
    """One visual line, already split into its columns."""

    top: float
    cells: list[str]

    @property
    def filled(self) -> int:
        return sum(1 for cell in self.cells if cell)

    def is_empty(self) -> bool:
        return self.filled == 0


@dataclass
class Table:
    """A run of rows sharing a column layout, with where it sat on the page."""

    rows: list[list[str]]
    top: float
    bottom: float
    columns: int = 0
    #: Rows whose cells were taken from more than one visual line, recorded so a caller
    #: can tell that the layout was reassembled rather than read straight off.
    merged_rows: int = 0
    cells: list[list[str]] = field(default_factory=list)


def is_numeric(text: str) -> bool:
    """Whether a cell reads as a figure rather than a word."""
    stripped = text.strip()
    if not stripped or NIL.match(stripped):
        return False
    return bool(NUMERIC.match(stripped))


def extract(page) -> list[Table]:
    """Every borderless table on a page, or an empty list.

    Returns nothing for a page of prose, which is the common case and the one where a
    false positive would do damage.
    """
    words = _words(page)
    if len(words) < MIN_TABLE_ROWS * 2:
        return []

    rows = _rows(words)
    if len(rows) < MIN_TABLE_ROWS:
        return []

    tables: list[Table] = []
    for band in _candidate_bands(rows):
        table = _build(band)
        if table is not None:
            tables.append(table)
    return tables


def _words(page) -> list[tuple[float, float, float, str]]:
    """(x0, x1, y0, text) for every word with ink in it."""
    try:
        raw = page.get_text("words")
    except Exception:  # noqa: BLE001 - a damaged page costs only itself
        return []
    return [
        (w[0], w[2], w[1], w[4].strip())
        for w in raw
        if len(w) >= 5 and str(w[4]).strip()
    ]


def _rows(words: list[tuple[float, float, float, str]]) -> list[list[tuple]]:
    """Words grouped by baseline, each row ordered left to right."""
    buckets: dict[int, list[tuple]] = {}
    for word in words:
        buckets.setdefault(round(word[2] / ROW_TOLERANCE), []).append(word)
    return [
        sorted(buckets[key], key=lambda w: w[0])
        for key in sorted(buckets)
    ]


def _candidate_bands(rows: list[list[tuple]]) -> list[list[list[tuple]]]:
    """Consecutive rows that might form one table, split where the layout changes.

    A blank stretch down the page ends a table, and so does a row that reaches into a
    corridor every other row leaves clear — which is what a paragraph does to a column
    layout.
    """
    if not rows:
        return []

    # One band for the whole page first; the column test below is what rejects prose.
    # Splitting on large vertical gaps keeps two separate tables on one page apart.
    spacings = sorted(
        b[0][2] - a[0][2] for a, b in zip(rows, rows[1:]) if b[0][2] > a[0][2]
    )
    typical = spacings[len(spacings) // 2] if spacings else ROW_TOLERANCE
    limit = max(typical * 3.0, ROW_TOLERANCE * 4)

    bands: list[list[list[tuple]]] = [[rows[0]]]
    for previous, current in zip(rows, rows[1:]):
        if current[0][2] - previous[0][2] > limit:
            bands.append([current])
        else:
            bands[-1].append(current)
    return [band for band in bands if len(band) >= MIN_TABLE_ROWS]


def _corridors(
    rows: list[list[tuple]], share: float = CORRIDOR_CLEAR_SHARE
) -> list[tuple[float, float]]:
    """The vertical lanes of whitespace that run down this block of rows.

    Built by projecting every word's horizontal extent onto one axis and looking at what
    is left uncovered. A lane counts only if it is clear in nearly every row, so the gap
    between two words in one sentence never becomes a column boundary.
    """
    spans = [(w[0], w[1]) for row in rows for w in row]
    if not spans:
        return []

    left = min(s[0] for s in spans)
    right = max(s[1] for s in spans)
    if right - left < MIN_COLUMN_GAP:
        return []

    # Per-row coverage, so a lane can be required to be clear in most rows rather than
    # merely clear once.
    step = 1.0
    width = int((right - left) / step) + 1
    clear_count = [0] * width
    for row in rows:
        covered = bytearray(width)
        for x0, x1 in ((w[0], w[1]) for w in row):
            start = max(0, int((x0 - left) / step))
            end = min(width - 1, int((x1 - left) / step))
            for index in range(start, end + 1):
                covered[index] = 1
        for index in range(width):
            if not covered[index]:
                clear_count[index] += 1

    needed = len(rows) * share
    lanes: list[tuple[float, float]] = []
    start: int | None = None
    for index in range(width):
        if clear_count[index] >= needed:
            if start is None:
                start = index
        elif start is not None:
            if (index - start) * step >= MIN_COLUMN_GAP:
                lanes.append((left + start * step, left + index * step))
            start = None
    if start is not None and (width - start) * step >= MIN_COLUMN_GAP:
        lanes.append((left + start * step, right))
    return lanes


def _boundaries(rows: list[list[tuple]], share: float = CORRIDOR_CLEAR_SHARE) -> list[float]:
    """Where one column ends and the next begins, taken from the corridors."""
    lanes = _corridors(rows, share)
    if not lanes:
        return []
    # The split goes down the middle of each corridor, so a value that overhangs its
    # header by a point or two still lands in the right column.
    return [(lane[0] + lane[1]) / 2 for lane in lanes]


def _conforms(row: list[tuple], boundaries: list[float]) -> bool:
    """Whether every word on this row sits wholly inside one column."""
    for x0, x1, _y, _text in row:
        if any(x0 < boundary < x1 for boundary in boundaries):
            return False
    return True


def _column_boundaries(rows: list[list[tuple]]) -> list[float]:
    """Where this block's columns divide, derived from the rows that agree.

    Found in two passes, because the first pass has to survive the page's own
    letterhead. A company name centred across the top spans every corridor beneath it,
    and a single such line is enough to erase a column from a strict projection — which
    is exactly what happened: a four-column statement came out as two, with the note
    references swept back into the labels.

    So the corridors are drawn permissively first, the rows that straddle them are set
    aside, and the boundaries are re-derived from the rows that agree with each other.

    The rows set aside are used, not discarded. They decide nothing about where the
    columns are, and then every row on the page is placed into those columns. Dropping
    them was tried and was worse than the flattening it replaced: a cash flow statement
    came back with most of its value lines missing, which reads as a complete table and
    is not one.
    """
    rough = _boundaries(rows, share=PERMISSIVE_CLEAR_SHARE)
    if not rough:
        return []

    conforming = [row for row in rows if _conforms(row, rough)]
    if len(conforming) < MIN_TABLE_ROWS or len(conforming) < len(rows) * MIN_CONFORMING_SHARE:
        # Most of the block disagrees with the layout, so there is no layout.
        return _boundaries(rows)

    return _boundaries(conforming)


def _join_cell(words: list[tuple[float, str]]) -> str:
    """The words of one cell, in reading order.

    Rows arrive sorted left to right, which is the reading order for a Latin cell and
    the reverse of it for an Arabic one. Joining an Arabic cell as it sits would put
    the end of the phrase first — a cell that looks like Arabic and says something
    else, which is worse than a cell that is obviously broken.
    """
    if not words:
        return ""
    plain = " ".join(text for _x, text in words)
    if is_rtl(plain):
        plain = " ".join(text for _x, text in sorted(words, key=lambda w: -w[0]))
    return repair_arabic(plain).strip()


def _column_of(x0: float, x1: float, boundaries: list[float], columns: int) -> int:
    """Which column a word belongs to, by how much of it lies in each.

    Overlap rather than midpoint. A long item name runs past the first boundary and its
    midpoint can land in the column of figures beside it — which is how a label ends up
    filed as an amount. Most of that word is still in the label column, and that is what
    decides.
    """
    edges = [float("-inf"), *boundaries, float("inf")]
    best, widest = 0, -1.0
    for index in range(columns):
        overlap = min(x1, edges[index + 1]) - max(x0, edges[index])
        if overlap > widest:
            best, widest = index, overlap
    return best


def _build(rows: list[list[tuple]]) -> Table | None:
    """One band of rows as a table, or None when it is not one."""
    boundaries = _column_boundaries(rows)
    if len(boundaries) < MIN_TABLE_COLUMNS - 1:
        return None

    columns = len(boundaries) + 1
    if columns < MIN_TABLE_COLUMNS:
        return None

    built: list[Row] = []
    for row in rows:
        cells = [""] * columns
        parts: list[list[tuple[float, str]]] = [[] for _ in range(columns)]
        for x0, x1, _y, text in row:
            parts[_column_of(x0, x1, boundaries, columns)].append((x0, text))
        for index, words in enumerate(parts):
            cells[index] = _join_cell(words)
        built.append(Row(top=row[0][2], cells=cells))

    merged, merged_count = _merge_continuations(built)
    if len(merged) < MIN_TABLE_ROWS:
        return None

    body = [r.cells for r in merged]
    if not _reads_as_a_table(body):
        return None

    return Table(
        rows=body,
        top=merged[0].top,
        bottom=merged[-1].top,
        columns=columns,
        merged_rows=merged_count,
        cells=body,
    )


def _merge_continuations(rows: list[Row]) -> tuple[list[Row], int]:
    """Fold a row that is only the rest of the row above it.

    Statements do this constantly: a long item name on one line and its figures on the
    next, or a note reference dropped half a line below the label it belongs to. Left
    apart, the figures become a row with no name — which is how an amount ends up
    attributed to whatever label happens to be nearest.

    A row merges up only into cells the row above left empty. The moment a cell would
    overwrite something, the row stands on its own, which is what keeps a subtotal line
    from being swallowed by the last item above it.
    """
    if not rows:
        return rows, 0

    spacings = sorted(b.top - a.top for a, b in zip(rows, rows[1:]) if b.top > a.top)
    typical = spacings[len(spacings) // 2] if spacings else ROW_TOLERANCE
    limit = typical * MAX_CONTINUATION_GAP

    merged: list[Row] = []
    count = 0
    for row in rows:
        if row.is_empty():
            continue
        if not merged:
            merged.append(row)
            continue

        previous = merged[-1]
        # Only a row with no label of its own is a continuation; anything that names
        # itself is a row in its own right.
        has_label = bool(row.cells[0])
        collides = any(cell and previous.cells[i] for i, cell in enumerate(row.cells))
        near = (row.top - previous.top) <= limit

        if not has_label and not collides and near:
            for index, cell in enumerate(row.cells):
                if cell:
                    previous.cells[index] = cell
            count += 1
            continue

        merged.append(row)
    return merged, count


def _reads_as_a_table(body: list[list[str]]) -> bool:
    """Whether this grid is a table rather than prose that happens to have a gap.

    Two things have to be true at once: enough rows must actually use more than one
    column, and enough of what is outside the first column must be figures. A page of
    indented prose satisfies neither.
    """
    if len(body) < MIN_TABLE_ROWS:
        return False

    multi = sum(1 for row in body if sum(1 for cell in row if cell) >= 2)
    if multi < MIN_TABLE_ROWS:
        return False

    trailing = [cell for row in body for cell in row[1:] if cell]
    if not trailing:
        return False

    figures = sum(1 for cell in trailing if is_numeric(cell) or NIL.match(cell.strip()))
    return figures / len(trailing) >= MIN_NUMERIC_SHARE
