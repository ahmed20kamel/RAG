"""Arithmetic the system does itself, and labels as its own.

Two different things can appear in an answer as a number. One was printed in a document
and can be pointed at. The other was worked out. They read identically once they are in
a sentence, and that is the whole problem: an answer that computes a per-metre rate and
states it beside a quoted contract value invites a reader to check neither.

So arithmetic is done here instead of being left to the model, under two rules.

It is deterministic. Every relation is found by summing numbers, not by asking anything
to reason about them, so the same evidence gives the same result on every run and the
operands are known rather than claimed.

It is never silent. A computed value carries the cells it came from, and it reaches the
prompt inside a block that says it was calculated — separate from the facts block, which
holds only what the documents printed. Nothing here fills a gap, guesses a missing
figure, or reports a relation it could not complete from values actually present.

What it looks for is structural, not subject matter. A column of figures in which one
value equals the sum of a run above it is a total — in a balance sheet, a bill of
quantities, a timesheet or a parts list. Nothing in this file knows which it is reading.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

#: Arabic-Indic digits map onto ASCII before anything tries to read a number.
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

#: A figure as documents write them: thousands separators, a decimal point, a leading
#: sign, or brackets — which is how accounts write a negative.
NUMBER = re.compile(r"^[(\[]?\s*[-+]?\d[\d,٬،\s]*(?:\.\d+)?\s*[)\]]?$")

#: A lone dash is how a document writes nil. It is a stated zero, not a missing value,
#: and treating it as missing would break every total that includes an empty line.
NIL = re.compile(r"^[-–—]$")

#: Runs longer than this are not reported. A "total" of forty lines is almost always a
#: coincidence of the search rather than a relation anyone intended.
MAX_OPERANDS = 20

#: Two or fewer numbers cannot make an interesting total: any value equals itself.
MIN_OPERANDS = 2

#: Findings per table. A cap, because a long schedule can contain many true relations
#: and the prompt has a budget.
MAX_FINDINGS_PER_TABLE = 12

#: Column headings that name a reference rather than a quantity. Adding these up
#: produces a true sum of meaningless things: in one test, note references 6 and 7 were
#: reported as totalling the note reference 13. That is the same failure this whole
#: layer exists to prevent, arriving from the other direction.
#:
#: A convention of documents, not a fact about any one of them — invoices, bills of
#: quantities, timesheets and statements all number their lines this way. It reads the
#: heading, never the values, so it cannot misfire on data.
#:
#: Known limit: a reference column with no heading is not recognised, and a coincidental
#: sum inside one can still be reported.
REFERENCE_HEADERS = frozenset({
    "note", "notes", "ref", "refs", "reference", "no", "no.", "s/n", "sn", "#",
    "item no", "item no.", "line", "line no", "code", "serial",
    "إيضاح", "إيضاحات", "ايضاح", "ملاحظة", "ملاحظات", "رقم", "مرجع", "بند", "م",
})

#: Exact comparison, with a hair of room for the rounding a document itself printed.
TOLERANCE = Decimal("0.5")


@dataclass(frozen=True)
class Operand:
    """One figure that went into a calculation, and where it was printed."""

    label: str
    text: str
    value: Decimal
    row: int


@dataclass
class Derived:
    """A value this system calculated, with everything needed to check it.

    Carries the operands rather than a summary of them, because a derived number whose
    inputs cannot be listed is not auditable, and an unauditable calculation is the
    thing this module exists to avoid producing.
    """

    kind: str
    label: str
    computed: Decimal
    stated: Decimal | None
    operands: list[Operand]
    column: str = ""
    citation: int = 0
    locator: str = ""

    @property
    def holds(self) -> bool:
        if self.stated is None:
            return False
        return abs(self.computed - self.stated) <= TOLERANCE

    @property
    def difference(self) -> Decimal:
        return Decimal(0) if self.stated is None else self.stated - self.computed

    def describe(self) -> str:
        """One line for the prompt, worded so it cannot be mistaken for a quotation."""
        joiner = " − " if self.kind == "difference" else " + "
        operands = joiner.join(o.text for o in self.operands)
        where = f" [{self.citation}]" if self.citation else ""
        column = f" ({self.column})" if self.column else ""
        if self.holds:
            return (
                f"محسوب: {self.label}{column} = {operands} = {_plain(self.computed)} "
                f"— يطابق القيمة المذكورة في المستند{where}"
            )
        return (
            f"محسوب: مجموع {operands} = {_plain(self.computed)}، بينما المذكور في "
            f"المستند لـ{self.label}{column} هو {_plain(self.stated)} — فرق "
            f"{_plain(abs(self.difference))}{where}"
        )


@dataclass
class Report:
    """Everything calculated for one answer."""

    derived: list[Derived] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.derived

    def render(self) -> str:
        """The block that goes into the prompt, or nothing at all.

        Kept apart from the facts block on purpose. The facts block is what the
        documents say; this is what was worked out from it, and the two must not be
        read as the same kind of statement.
        """
        if not self.derived:
            return ""
        lines = [
            "=== قيم محسوبة (أجراها النظام، وليست مقتبسة من المستند) ===",
            "(كل سطر أدناه ناتج عملية حسابية على قيم واردة في المصادر أعلاه. "
            "عند ذكر أي منها في الإجابة، اذكر صراحةً أنها محسوبة لا منقولة.)",
        ]
        lines.extend(f"- {item.describe()}" for item in self.derived)
        return "\n".join(lines) + "\n\n"


def _plain(value: Decimal) -> str:
    """A number written back the way a document would write it."""
    quantised = value.normalize()
    text = f"{quantised:,f}" if quantised == quantised.to_integral_value() else f"{quantised:,}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_number(text: str) -> Decimal | None:
    """A cell as a number, or None when it is not one.

    A dash is nil, which is zero. Anything else that is not a bare figure — a note
    reference like "6-A", a year in a heading, a word — is not a number for this
    purpose and is left out rather than guessed at.
    """
    stripped = (text or "").strip().translate(ARABIC_DIGITS)
    if not stripped:
        return None
    if NIL.match(stripped):
        return Decimal(0)
    if not NUMBER.match(stripped):
        return None

    negative = stripped.startswith("(") or stripped.startswith("[")
    cleaned = re.sub(r"[(),\[\]\s٬،]", "", stripped)
    if not cleaned or cleaned in {"-", "+"}:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative and value > 0 else value


def verify_grid(
    grid: list[list[str]], *, citation: int = 0, locator: str = ""
) -> list[Derived]:
    """Every total in a table that can be confirmed by adding the figures above it.

    Searched per column, because a column is what a total totals. For each numeric cell
    the runs of cells directly above it are summed and compared; a run that matches is
    reported with its operands, and one that does not is not reported at all.

    Not reporting a mismatch is deliberate. Any value can be made to miss some sum, and
    a list of sums that a number is not would be noise indistinguishable from a finding.
    What is asserted here is only what was verified.
    """
    if len(grid) < MIN_OPERANDS + 1:
        return []

    width = max(len(row) for row in grid)
    headers = grid[0] if grid else []
    findings: list[Derived] = []

    for column in range(1, width):
        cells: list[tuple[int, str, Decimal]] = []
        for index, row in enumerate(grid):
            if column >= len(row):
                continue
            value = parse_number(row[column])
            if value is not None:
                cells.append((index, row[column].strip(), value))

        if len(cells) < MIN_OPERANDS + 1:
            continue

        column_name = headers[column].strip() if column < len(headers) else ""
        if _is_reference_column(column_name):
            continue
        explained = _sums_in_column(grid, cells, column_name, citation, locator)
        findings.extend(explained)
        findings.extend(
            _differences_in_column(
                grid, cells, column_name, citation, locator,
                {d.label for d in explained},
            )
        )

    # The same total can be reachable by more than one run when a column holds
    # repeated nils; reporting it twice adds nothing but length.
    unique: list[Derived] = []
    seen: set[tuple] = set()
    for item in findings:
        key = (item.label, item.column, str(item.computed))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:MAX_FINDINGS_PER_TABLE]


def _is_reference_column(header: str) -> bool:
    """Whether a column's heading says it holds references rather than amounts."""
    cleaned = header.strip().strip(":.").lower()
    return bool(cleaned) and cleaned in REFERENCE_HEADERS


def _sums_in_column(
    grid: list[list[str]],
    cells: list[tuple[int, str, Decimal]],
    column_name: str,
    citation: int,
    locator: str,
) -> list[Derived]:
    """Totals found among one column's figures."""
    found: list[Derived] = []
    used_rows: set[int] = set()

    # Later cells first: a grand total is further down than the subtotals inside it, and
    # finding it first keeps the longer, more informative relation.
    for position in range(len(cells) - 1, MIN_OPERANDS - 1, -1):
        row_index, text, stated = cells[position]
        if row_index in used_rows or stated == 0:
            continue

        best: list[tuple[int, str, Decimal]] | None = None
        start = max(0, position - MAX_OPERANDS)
        for begin in range(position - MIN_OPERANDS, start - 1, -1):
            run = cells[begin:position]
            if len(run) < MIN_OPERANDS:
                continue
            if abs(sum(c[2] for c in run) - stated) <= TOLERANCE:
                best = run  # keep searching: a longer run is the better explanation

        if best is None:
            continue

        # Nils at either end add nothing and make the line unreadable: a total shown as
        # "- + - + 30,166 + - + 150,000" hides the two figures that matter.
        while best and best[0][2] == 0:
            best = best[1:]
        while best and best[-1][2] == 0:
            best = best[:-1]
        if len(best) < MIN_OPERANDS:
            continue

        if _is_running_index([c[2] for c in best], stated):
            # A table of contents lists pages 1, 2, 3 — and 1 + 2 = 3. True, and about
            # nothing. A sequence of consecutive integers is a numbering, not a total,
            # whatever column it sits in.
            continue

        # A statement prints subtotals on rows with no name of their own. Skipping
        # them loses real relations; naming them by their own figure produces
        # "180,166 = … = 180,166", which is true and unreadable. So an unnamed row is
        # described by where it is instead.
        label = _label(grid, row_index) or text
        if parse_number(label) is not None or not label:
            label = "المجموع الفرعي في هذا الصف"

        operands = [
            Operand(label=_label(grid, c[0]), text=c[1], value=c[2], row=c[0])
            for c in best
        ]
        found.append(
            Derived(
                kind="column_sum",
                label=label,
                computed=sum(o.value for o in operands),
                stated=stated,
                operands=operands,
                column=column_name,
                citation=citation,
                locator=locator,
            )
        )
        used_rows.update(c[0] for c in best)
        used_rows.add(row_index)

    return list(reversed(found))


#: A difference is checked only against the figures near it. Two numbers far apart in
#: a long column will eventually differ by a third one by chance, and a coincidence
#: reported as a verified relation is worse than a relation missed.
DIFFERENCE_WINDOW = 8


def _differences_in_column(
    grid: list[list[str]],
    cells: list[tuple[int, str, Decimal]],
    column_name: str,
    citation: int,
    locator: str,
    already_explained: set[str],
) -> list[Derived]:
    """Values that are the difference between two figures above them.

    A statement's most important line is often a subtraction, not a sum: profit is
    revenue less expenses, and net movement is opening less closing. Checking only
    sums left the one relation a reader most wants verified unverified.

    Deliberately narrow. Only the nearest figures are considered, both operands and the
    result must be non-zero and distinct, and a row already explained by a sum is left
    alone — a value with two competing explanations is not evidence of either.
    """
    found: list[Derived] = []
    for position in range(2, len(cells)):
        row_index, _text, stated = cells[position]
        if stated == 0:
            continue
        label = _label(grid, row_index)
        if not label or parse_number(label) is not None or label in already_explained:
            continue

        window = cells[max(0, position - DIFFERENCE_WINDOW) : position]
        match: tuple[tuple, tuple] | None = None
        for i, minuend in enumerate(window):
            for subtrahend in window[i + 1 :]:
                if minuend[2] == 0 or subtrahend[2] == 0:
                    continue
                if minuend[2] == subtrahend[2] or minuend[2] == stated:
                    continue
                if abs((minuend[2] - subtrahend[2]) - stated) <= TOLERANCE:
                    match = (minuend, subtrahend)
                    break
            if match:
                break

        if match is None:
            continue
        minuend, subtrahend = match
        if _is_running_index([minuend[2], subtrahend[2]], stated):
            continue

        found.append(
            Derived(
                kind="difference",
                label=label,
                computed=minuend[2] - subtrahend[2],
                stated=stated,
                operands=[
                    Operand(_label(grid, minuend[0]), minuend[1], minuend[2], minuend[0]),
                    Operand(_label(grid, subtrahend[0]), subtrahend[1], subtrahend[2], subtrahend[0]),
                ],
                column=column_name,
                citation=citation,
                locator=locator,
            )
        )
    return found


def _is_running_index(operands: list[Decimal], stated: Decimal) -> bool:
    """Whether these figures are a numbering rather than quantities.

    Page numbers, line numbers and item numbers run 1, 2, 3, and the first two of them
    always sum to the third. The relation is arithmetically true and says nothing.
    """
    values = sorted(operands + [stated])
    if len(values) < 3:
        values = sorted(operands)
    if any(v != v.to_integral_value() or v <= 0 or v > 1000 for v in values):
        return False
    return all(b - a == 1 for a, b in zip(values, values[1:]))


def _label(grid: list[list[str]], row: int) -> str:
    """The name of a row, which is whatever its first non-empty cell says."""
    if row >= len(grid):
        return ""
    for cell in grid[row]:
        if cell.strip():
            return cell.strip()
    return ""


class ArithmeticVerifier:
    """Runs the checks over whatever tables an answer's evidence contains."""

    def verify(self, context: str, sources: list | None = None) -> Report:
        """Every confirmable total in the context the model is about to be given.

        Reads the assembled context rather than the citation list. The citations carry
        a truncated excerpt — enough to show a reader where a passage came from, and
        not enough to hold a whole table, so checking them found nothing on a document
        whose tables were extracted perfectly well. What is verified here is exactly the
        text the model sees, which is the only reading a check about that answer can
        honestly be made against.
        """
        report = Report()
        locators = {
            int(getattr(s, "citation", 0) or 0): str(getattr(s, "locator", "") or "")
            for s in (sources or [])
        }
        for citation, body in _blocks(context):
            grid = _grid_from_text(body)
            if not grid:
                continue
            try:
                report.derived.extend(
                    verify_grid(grid, citation=citation, locator=locators.get(citation, ""))
                )
            except Exception:  # noqa: BLE001 - a bad table costs only itself
                logger.exception("Arithmetic verification failed for citation %s", citation)

        if report.derived:
            holding = sum(1 for d in report.derived if d.holds)
            logger.info(
                "Arithmetic: %s relation(s) verified, %s hold",
                len(report.derived), holding,
            )
        return report


#: Each context block opens with its citation number and the file it came from.
BLOCK_HEADER = re.compile(r"^\[(\d+)\]\s", re.MULTILINE)


def _blocks(context: str) -> list[tuple[int, str]]:
    """The context split back into the passages it was built from, with their numbers."""
    if not context:
        return []
    found: list[tuple[int, str]] = []
    matches = list(BLOCK_HEADER.finditer(context))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(context)
        found.append((int(match.group(1)), context[match.end() : end]))
    return found


def _grid_from_text(content: str) -> list[list[str]]:
    """Markdown pipe rows as a grid, or nothing.

    Reading the rendered text rather than a stored structure keeps this working for
    every format at once: a workbook, a Word table and a borderless PDF all arrive here
    as the same pipe rows, because that is what the parsers were made to produce.
    """
    rows: list[list[str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} for c in cells if c):
            continue  # the header separator
        rows.append(cells)
    return rows if len(rows) >= MIN_OPERANDS + 1 else []
