"""Whether the text a PDF gives up is the text it shows.

Some typed PDFs carry a text layer that does not match their glyphs. Measured on the case
files: an expert report whose words come out letter by letter with phonetic and Persian
symbols in them ("ا ل خ ب رة", "ع ڴ ʄ س ؈ف"), with a date missing altogether, and letters
whose "لا" comes out reversed ("االستشاري", "المقاوالت", "مالحظات"). The page looks perfect;
the text behind it is what the search and the model read.

Such a page is better read the way a person reads it: rendered and recognised. On a clean
digital render that recognition is accurate, unlike on a scan. This module only measures;
the parser decides.
"""

from __future__ import annotations

import re

_ARABIC_LETTER = re.compile(r"[ء-ي]")
#: Letters from the Arabic block's extended ranges and from phonetic alphabets, which
#: appear in broken layers where an ordinary Arabic letter should be.
_FOREIGN = re.compile(r"[ٱ-ۿݐ-ݿࢠ-ࣿɐ-ʯƀ-ɏ଀-୿]")
_ARABIC_TOKEN = re.compile(r"^[ء-يٱ-ۿ]+$")
#: A word that starts alef-alef ("االستشاري", "األضرار") — impossible in written Arabic,
#: the mark of a "لا" stored reversed after the article.
_REVERSED_LAM_ALEF = re.compile(r"^(?:[وفبكل])?ا[اأإآ]")
#: Arabic words of one letter that are words: "و" and the like are not damage. "ا" too:
#: the alef of a tanween ("مترًا") comes out on its own from some fonts, harmlessly.
_SINGLE_WORDS = {"و", "ف", "ب", "ك", "ل", "أ", "م", "ه", "ق", "ا"}


def damage(text: str) -> float:
    """How broken an Arabic text layer looks, from 0 (clean) upwards. Latin text and
    numbers do not count either way."""
    letters = _ARABIC_LETTER.findall(text)
    if len(letters) < 40:
        return 0.0
    tokens = [t.strip(".,:،؛()[]«»\"'-–—") for t in text.split()]
    arabic = [t for t in tokens if t and _ARABIC_TOKEN.match(t)]
    if not arabic:
        return 0.0
    foreign = len(_FOREIGN.findall(text)) / max(1, len(letters))
    split = sum(1 for t in arabic if len(t) == 1 and t not in _SINGLE_WORDS) / len(arabic)
    reversed_lam_alef = sum(1 for t in arabic if _REVERSED_LAM_ALEF.match(t)) / len(arabic)
    return round(foreign * 4 + split + reversed_lam_alef * 3, 4)


#: Above this a page is re-read as an image.
DAMAGED = 0.08


def arabic_words(text: str) -> int:
    return sum(1 for t in text.split() if _ARABIC_TOKEN.match(t.strip(".,:،؛()[]«»\"'-–—")))
