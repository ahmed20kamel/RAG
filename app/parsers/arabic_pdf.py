"""Putting Arabic back in reading order after a PDF took it out of one.

A PDF stores glyphs at positions, not a sentence. Some generators write Arabic in logical
order and let the viewer lay it out right to left; others write it already laid out, in
visual order, and the text a reader sees comes back with its words reversed. Both produce
files that look perfect on screen and extract differently.

This was measured on real files before it was written, because the wrong repair is worse
than none. A supplier invoice extracted as «معلومات تقنية خدمات تقديم عقد» is the phrase
«عقد تقديم خدمات تقنية معلومات» backwards — every word intact, the sentence inside out.

Three things were tried against those files:

* the bidi algorithm, applied with `get_display` — it makes it worse, because the text is
  already visual and that function goes the other way;
* sorting glyphs by x-coordinate — correct word order, but it reverses digits, turning
  "20 مايو 2026" into "02 مايو 6202";
* sorting *words* by x-coordinate — correct, and the only one that survives both cases.

The last is what runs here. Sorting whole words leaves each word's own characters alone,
so a number stays a number and an English word stays readable, while their positions in
the line are reordered to match how the line is actually read.

Applied only to lines that are mostly Arabic. Sorting an English line right to left would
reverse a perfectly good sentence, which is the failure this file exists to prevent.
"""

from __future__ import annotations

import re
from collections import defaultdict

from app.parsers.text_repair import repair_arabic

ARABIC_LETTER = re.compile(r"[؀-ۿݐ-ݿﭐ-﻿]")
LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

#: A line is read right to left when this share of its letters is Arabic. Set above a
#: half so a mostly-English line with one Arabic word is left alone.
RTL_SHARE = 0.5

#: Words whose vertical positions differ by less than this are on the same line. PDFs
#: rarely align a line's glyphs to the same y exactly.
LINE_TOLERANCE = 3.0


def is_rtl(text: str) -> bool:
    """Whether this text reads right to left, by counting letters rather than guessing."""
    letters = LETTER.findall(text)
    if not letters:
        return False
    arabic = sum(1 for ch in letters if ARABIC_LETTER.match(ch))
    return arabic / len(letters) > RTL_SHARE


def page_text(page) -> str:
    """A page's text in reading order, with Arabic repaired."""
    return "\n".join(line["text"] for line in page_lines(page) if line["text"].strip())


def page_lines(page) -> list[dict]:
    """Each line of a page: its text in reading order, and the size it was drawn at.

    One function rather than two on purpose. Text and font size used to be read through
    separate PyMuPDF calls — words for the text, spans for the sizes — and the two
    disagreed on Arabic badly enough that heading detection never matched a single line:
    the same line came back as "تقرير المشروع السنوي" from one and reversed from the
    other. Headings were found and then silently discarded, and every document collapsed
    into one section.

    So the text is taken from the word path, which is the one measured to be correct, and
    the size is looked up from the span path by matching vertical position. Neither is
    asked to do the other's job.

    Words are grouped by the block and line numbers PyMuPDF reports, so a two-column
    layout does not have its columns interleaved by a sort across the page.
    """
    try:
        words = page.get_text("words")
    except Exception:  # noqa: BLE001 - a damaged page should cost only itself
        return []

    if not words:
        return []

    # (block, line) is the layout unit; y is kept so the lines can be ordered down the page.
    grouped: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for word in words:
        x0, y0, _x1, _y1, text, block, line, _no = word[:8]
        if text.strip():
            grouped[(block, line)].append((x0, y0, text))

    styles = _styles_by_position(page)

    lines: list[dict] = []
    for parts in grouped.values():
        top = min(p[1] for p in parts)
        left = min(p[0] for p in parts)
        joined = " ".join(p[2] for p in parts)
        if is_rtl(joined):
            # Right to left: the rightmost word is the first one read.
            ordered = " ".join(p[2] for p in sorted(parts, key=lambda p: -p[0]))
        else:
            ordered = " ".join(p[2] for p in sorted(parts, key=lambda p: p[0]))

        size, bold = styles.get(_row(top), (0.0, False))
        lines.append(
            {
                "text": repair_arabic(re.sub(r"\s+", " ", ordered)).strip(),
                "size": size,
                "bold": bold,
                "top": top,
                "left": left,
                "rtl": is_rtl(joined),
            }
        )

    # Down the page, then across it — and "across" means right to left for Arabic.
    # PyMuPDF often reports one visual line as two groups, splitting where the shaping
    # changes, and both carry the same y. Ordering those two by ascending x put the end
    # of an Arabic sentence before its beginning, which read as a sentence and was not
    # one. The direction of the line decides which way across means.
    lines.sort(
        key=lambda line: (
            _row(line["top"]),
            -line["left"] if line["rtl"] else line["left"],
        )
    )
    return lines


def _row(top: float) -> int:
    """A vertical bucket, so two readings of the same line agree on which line it is."""
    return round(top / LINE_TOLERANCE)


def _styles_by_position(page) -> dict[int, tuple[float, bool]]:
    """Font size and weight for each line, keyed by the same vertical bucket.

    A PDF has no headings — only text somebody set larger or bolder than what surrounds
    it — so these are the only structural signal available.
    """
    try:
        data = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return {}

    found: dict[int, tuple[float, bool]] = {}
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            key = _row(line.get("bbox", [0, 0, 0, 0])[1])
            size = round(max(s.get("size", 0) for s in spans), 1)
            bold = any(
                "bold" in str(s.get("font", "")).lower() or bool(s.get("flags", 0) & 2**4)
                for s in spans
            )
            # Keep the largest reading for a bucket: a line with a mix of sizes takes
            # its heading candidacy from the biggest text on it.
            previous = found.get(key)
            if previous is None or size > previous[0]:
                found[key] = (size, bold)
    return found
