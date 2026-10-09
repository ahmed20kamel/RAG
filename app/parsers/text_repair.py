"""Repairing text that a file format damaged on its way out.

Shared by every binary parser, and kept out of `app/core/text.py` on purpose: that module
defines how the whole system normalises Arabic for matching, and a parser must not be
able to change it. What happens here is narrower and earlier — the text is put back into
the form it was written in, and `core.text` then normalises it like anything else.

Two repairs, both measured against real files rather than assumed.

Presentation forms. Arabic is stored in some files as the contextual glyphs a renderer
produced — "ﻧﻴﺎﺑﺔ ﻣﺮﻭﺭ" instead of "نيابة مرور". Every downstream comparison is done on
ordinary Arabic letters, so text left in this form matches nothing: not the keyword
index, not an entity pattern, not a question. NFKC maps the glyphs back. Found in a PDF
first, then in the cells of a supplier's Excel file, which is why this is not in the PDF
parser where it started.

Spreadsheet errors. `#REF!` and its relatives are what a formula leaves behind when the
thing it pointed at is gone. With cached values they arrive looking exactly like data,
and a broken reference indexed as a quantity is worse than a blank one, because a blank
cell is visibly missing while "#REF!" can be cited.
"""

from __future__ import annotations

import re
import unicodedata

#: Arabic presentation forms A and B. Cheap to test for, and worth testing for: NFKC
#: normalises far more than Arabic, so it is applied only where it is needed.
PRESENTATION_FORMS = re.compile(r"[ﭐ-﷿ﹰ-﻿]")

TATWEEL = "ـ"

#: Invisible bidi controls. They carry no meaning a reader sees, and OCR emits them
#: around Latin runs inside Arabic text; left in, they sit inside words and stop a term
#: matching the same term typed normally.
BIDI_CONTROLS = re.compile(r"[‎‏‪-‮⁦-⁩​﻿]")

#: A soft hyphen between two characters of a word or code. PDF producers emit these where
#: a real hyphen was typed, and a reference number carrying one — "CMN­2026­0817" —
#: matches nothing a person types. Restored to a real hyphen rather than deleted: a
#: genuinely hyphenated line break becomes "infor-mation" instead of "information", which
#: is a visible oddity, while deleting it would silently corrupt every code in the corpus.
SOFT_HYPHEN_INSIDE = re.compile(r"(?<=[^\W_])­(?=[^\W_])", re.UNICODE)
SOFT_HYPHEN = "­"

#: Every error a spreadsheet cell can hold. None of them is a value.
SPREADSHEET_ERRORS = frozenset({
    "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!", "#NUM!",
    "#GETTING_DATA", "#SPILL!", "#CALC!", "#FIELD!", "#BLOCKED!", "#CONNECT!",
    "#UNKNOWN!", "#BUSY!",
})


def repair_arabic(text: str) -> str:
    """Contextual Arabic glyphs turned back into ordinary letters.

    Applied only when a presentation form is actually present, so text that never had
    the problem is returned untouched — NFKC would otherwise rewrite ligatures, widths
    and compatibility characters across the whole corpus for no reason.

    The tatweel goes too. It is a typographic stretch with no linguistic content, and
    "ﺗﻤــﺖ" and "تمت" must not be two different words to a keyword index.
    """
    if not text:
        return text
    cleaned = BIDI_CONTROLS.sub("", text)
    if SOFT_HYPHEN in cleaned:
        cleaned = SOFT_HYPHEN_INSIDE.sub("-", cleaned).replace(SOFT_HYPHEN, "")
    if not PRESENTATION_FORMS.search(cleaned):
        return cleaned
    return unicodedata.normalize("NFKC", cleaned).replace(TATWEEL, "")


def is_error_value(value: object) -> bool:
    """Whether a cell holds a spreadsheet error rather than a value."""
    if value is None:
        return False
    return str(value).strip().upper() in SPREADSHEET_ERRORS


def cell_value(value: object) -> str:
    """One cell as text: errors dropped, Arabic repaired, nothing else changed.

    Numbers and dates are stringified by the caller's `str`, which keeps whatever
    precision openpyxl handed over. Rounding or reformatting here would quietly change
    a figure that somebody is going to be cited on.
    """
    if value is None or is_error_value(value):
        return ""
    return repair_arabic(str(value)).strip()


# -- Tables stored in drawing order -------------------------------------------------
#
# Measured on the case files: the table cells of ten typed PDFs came out in the order the
# glyphs are drawn, left to right — "ةربخلا مسا" for "اسم الخبرة", the expert's name as
# "ن؈هاش دمحأ دمحم دمحم ماصع" — while the same page's running text read correctly. The
# search matched none of those words and the model quoted them backwards.

_ARABIC_WORD = re.compile(r"[ء-يٱ-ۿ؈]+")
#: Read backwards, the article ends a word and the tied ta begins one; neither happens
#: in written Arabic, both in every reversed table.
_BACKWARDS = re.compile(r"^[ةى]|..لا$")
_FORWARDS = re.compile(r"^ال..|[ةى]$")
#: Numbers, codes and Latin words: already left to right inside a reversed line, so they
#: are turned back after the line is.
_LEFT_TO_RIGHT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9 .,%:@_/\-]*[A-Za-z0-9%])?")
#: Persian letter shapes and a broken glyph the same fonts emit for Arabic letters.
_ARABIC_SHAPES = str.maketrans({"ی": "ي", "ھ": "ه", "ک": "ك", "؈": "ي"})
#: A letter left on its own by a space the drawing order put inside a word ("البن د").
_LONE_LETTER = re.compile(r"(?<=[ء-ي]{2}) ([ء-نهي])(?=$|[^ء-ي])")


def _direction(text: str) -> tuple[int, int]:
    words = _ARABIC_WORD.findall(text)
    return (sum(1 for w in words if _BACKWARDS.search(w)), sum(1 for w in words if _FORWARDS.search(w)))


def visual_to_logical(text: str) -> str:
    """A line stored in drawing order, put back into reading order."""
    turned = _LEFT_TO_RIGHT.sub(lambda m: m.group(0)[::-1], text[::-1])
    return _LONE_LETTER.sub(r"\1", turned.translate(_ARABIC_SHAPES))


def logical_grid(grid: list[list[str]]) -> list[list[str]]:
    """A table whose Arabic was stored in drawing order, read the right way round.

    Decided for the whole table, not per cell: a cell holding only a name carries no
    sign of its direction, and it is reversed exactly when its neighbours are.
    """
    backwards = forwards = 0
    for row in grid:
        for cell in row:
            b, f = _direction(cell)
            backwards += b
            forwards += f
    if backwards < 2 or backwards <= forwards:
        return grid
    return [[visual_to_logical(cell) if _ARABIC_WORD.search(cell) else cell for cell in row] for row in grid]


# -- Letters swapped by ligatures ---------------------------------------------------

def _swaps(word: str):
    """The word with one, then two, neighbouring letters exchanged."""
    once = []
    for i in range(len(word) - 1):
        once.append((i, word[:i] + word[i + 1] + word[i] + word[i + 2:]))
    for _, candidate in once:
        yield candidate
    for i, first in once:
        for j in range(i + 2, len(word) - 1):
            yield first[:j] + first[j + 1] + first[j] + first[j + 2:]


def unswap_letters(text: str, vocabulary) -> str:
    """Words a ligature font stored with neighbouring letters swapped, put back.

    `vocabulary` counts the words the same document reads as when recognised from its
    image: a word is changed only when it is not among them and exactly a swap or two
    away from one that is — the most frequent such, so the change is the document's own
    spelling and never a guess.
    """
    def fix(match: re.Match) -> str:
        word = match.group(0)
        if len(word) < 3 or vocabulary.get(word):
            return word
        best = max(_swaps(word), key=lambda c: vocabulary.get(c, 0), default=word)
        return best if vocabulary.get(best) else word

    return re.sub(r"[ء-ي]+", fix, text)
