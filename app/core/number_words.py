"""Numbers written as words, read as the values they state.

A contract says "مئة وعشرون يومًا" where a table says "120". Every deterministic layer
here works on digits — the answer validator, the keyword index, the conflict detector —
so without this a value spelled out is invisible to all of them: the source says "مئة
وعشرون يومًا", the model answers "120 يومًا", and the validator reports 120 as a value
the sources do not contain, although the answer is right.

This module does one thing: find the spans that state a number in words and give their
value. It never rewrites text. Callers use the values to *extend* what they compare
against — a haystack gains "30" beside "ثلاثون" — so nothing that matched before can stop
matching.

Deliberately conservative where Arabic is ambiguous:

* "عشر" alone is not ten. It is also "عُشر", one tenth, and "عشر قيمة العقد" is a
  fraction. Ten is read only from "عشرة", or as the second word of a teen.
* "واحد", "أحد" and "اثنين" alone are not read. "كل واحد", "يوم الأحد" and "الاثنين"
  are words, not quantities. They count inside a longer phrase ("واحد وعشرون").
* Duals of units ("يومين", "شهران") are read as two, because that is the only way Arabic
  says "two days" and it is exactly what a deadline clause uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import normalize

# -- Arabic lexicon, in normalised spelling (alef folded, ة→ه, ى→ي) ------------------

_UNITS: dict[str, int] = {
    "واحد": 1, "واحده": 1, "احد": 1, "احدي": 1,
    "اثنان": 2, "اثنين": 2, "اثنتان": 2, "اثنتين": 2, "اثنا": 2, "اثنتا": 2,
    "اثني": 2, "اثنتي": 2,
    "ثلاث": 3, "ثلاثه": 3,
    "اربع": 4, "اربعه": 4,
    "خمس": 5, "خمسه": 5,
    "ست": 6, "سته": 6,
    "سبع": 7, "سبعه": 7,
    "ثمان": 8, "ثماني": 8, "ثمانيه": 8,
    "تسع": 9, "تسعه": 9,
}
#: Read only inside a longer phrase — alone they are words, not quantities.
_WEAK_ALONE = {"واحد", "واحده", "احد", "احدي", "اثنين", "اثنتين", "اثني", "اثنتي",
               "اثنا", "اثنتا", "ست", "ثماني"}

_TEN_ALONE = {"عشره": 10}
_TEEN_MARKERS = {"عشر", "عشره"}

_TENS: dict[str, int] = {
    "عشرون": 20, "عشرين": 20,
    "ثلاثون": 30, "ثلاثين": 30,
    "اربعون": 40, "اربعين": 40,
    "خمسون": 50, "خمسين": 50,
    "ستون": 60, "ستين": 60,
    "سبعون": 70, "سبعين": 70,
    "ثمانون": 80, "ثمانين": 80,
    "تسعون": 90, "تسعين": 90,
}

_HUNDRED_WORDS = {"ميه", "مايه"}
_HUNDREDS: dict[str, int] = {"ميه": 100, "مايه": 100,
                             "ميتان": 200, "مايتان": 200, "ميتين": 200, "مايتين": 200}
for _stem, _value in (("ثلاث", 3), ("اربع", 4), ("خمس", 5), ("ست", 6), ("سبع", 7),
                      ("ثمان", 8), ("ثماني", 8), ("تسع", 9)):
    for _hundred in _HUNDRED_WORDS:
        _HUNDREDS[_stem + _hundred] = _value * 100

#: Multipliers. The dual and the accusative forms carry their own count.
_SCALES: dict[str, int] = {
    "الف": 1_000, "الفا": 1_000, "الاف": 1_000, "الوف": 1_000,
    "مليون": 1_000_000, "مليونا": 1_000_000, "ملايين": 1_000_000,
    "مليار": 1_000_000_000, "مليارا": 1_000_000_000, "مليارات": 1_000_000_000,
}
_SCALE_DUALS: dict[str, int] = {
    "الفان": 2_000, "الفين": 2_000,
    "مليونان": 2_000_000, "مليونين": 2_000_000,
    "ملياران": 2_000_000_000, "مليارين": 2_000_000_000,
}

#: "Two <unit>" said the only way Arabic says it.
_UNIT_DUALS = {
    "يومان", "يومين", "شهران", "شهرين", "اسبوعان", "اسبوعين", "سنتان", "سنتين",
    "عامان", "عامين", "ساعتان", "ساعتين", "دقيقتان", "دقيقتين", "مرتان", "مرتين",
}

# -- English -------------------------------------------------------------------------

_EN_SMALL: dict[str, int] = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_EN_WEAK = {"one": 1}
_EN_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000,
              "billion": 1_000_000_000}

_TOKEN = re.compile(r"[\w؀-ۿ]+(?:-[a-z]+)?", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SpelledNumber:
    """A run of words stating one number."""

    words: str
    value: int
    start: int
    end: int


def _bare(token: str) -> str:
    """The word without a leading conjunction or article, when that leaves a number."""
    if token in _LEXICON:
        return token
    for prefix in ("وال", "و", "ال", "بال", "لل"):
        if token.startswith(prefix) and token[len(prefix):] in _LEXICON:
            return token[len(prefix):]
    return token


_LEXICON = (
    set(_UNITS) | set(_TEN_ALONE) | _TEEN_MARKERS | set(_TENS) | set(_HUNDREDS)
    | set(_SCALES) | set(_SCALE_DUALS)
)


def _arabic_run(words: list[str]) -> tuple[int, int] | None:
    """(value, words consumed) for the phrase starting at words[0], or None."""
    total = 0
    current = 0
    used = 0
    meaningful = 0
    i = 0
    while i < len(words):
        word = _bare(words[i])

        # "خمسة و عشرون" — the conjunction written as its own word.
        if word == "و" and used and i + 1 < len(words) and _bare(words[i + 1]) in _LEXICON:
            i += 1
            continue

        if word in _UNITS and i + 1 < len(words) and _bare(words[i + 1]) in _TEEN_MARKERS:
            current += _UNITS[word] + 10
            i += 2
        elif word in _UNITS and i + 1 < len(words) and _bare(words[i + 1]) in _HUNDRED_WORDS:
            current += _UNITS[word] * 100
            i += 2
        elif word in _UNITS:
            current += _UNITS[word]
            i += 1
        elif word in _TEN_ALONE:
            current += 10
            i += 1
        elif word in _TENS:
            current += _TENS[word]
            i += 1
        elif word in _HUNDREDS:
            current += _HUNDREDS[word]
            i += 1
        elif word in _SCALE_DUALS:
            # Arabic states the larger part first ("ألفان وخمسمئة"), so anything already
            # counted belongs beside the dual, not multiplied by it.
            total += current + _SCALE_DUALS[word]
            current = 0
            i += 1
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
            i += 1
        else:
            break
        meaningful += 1
        used = i

    if not used:
        return None
    if meaningful == 1 and used == 1 and _bare(words[0]) in _WEAK_ALONE:
        return None
    if meaningful == 1 and _bare(words[0]) in _TEEN_MARKERS:
        return None
    return total + current, used


def _english_run(words: list[str]) -> tuple[int, int] | None:
    total = 0
    current = 0
    used = 0
    i = 0
    while i < len(words):
        word = words[i]
        parts = word.split("-")
        if len(parts) == 2 and parts[0] in _EN_SMALL and parts[1] in _EN_SMALL:
            current += _EN_SMALL[parts[0]] + _EN_SMALL[parts[1]]
        elif word in _EN_SMALL:
            current += _EN_SMALL[word]
        elif word in _EN_WEAK and i + 1 < len(words) and words[i + 1] in _EN_SCALES:
            current += 1
        elif word == "and" and used and i + 1 < len(words) and (
            words[i + 1] in _EN_SMALL or words[i + 1].split("-")[0] in _EN_SMALL
        ):
            i += 1
            continue
        elif word in _EN_SCALES and used:
            if _EN_SCALES[word] == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * _EN_SCALES[word]
                current = 0
        else:
            break
        i += 1
        used = i
    if not used:
        return None
    return total + current, used


def spelled_numbers(text: str) -> list[SpelledNumber]:
    """Every run of words in `text` that states a number, with its value.

    Offsets refer to the *normalised* text, which is what every caller compares against.
    """
    normalised = normalize(text)
    tokens = [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(normalised)]
    words = [t[0] for t in tokens]
    found: list[SpelledNumber] = []
    i = 0
    while i < len(tokens):
        word = words[i]
        if word in _UNIT_DUALS:
            found.append(SpelledNumber(word, 2, tokens[i][1], tokens[i][2]))
            i += 1
            continue
        run = _arabic_run(words[i:]) or _english_run(words[i:])
        if run is None:
            i += 1
            continue
        value, used = run
        start, end = tokens[i][1], tokens[i + used - 1][2]
        found.append(SpelledNumber(normalised[start:end], value, start, end))
        i += used
    return found


def digit_forms(text: str) -> str:
    """The values `text` states in words, as digits, space-separated. Empty if none.

    Appended to a haystack so that a digit in an answer or a query finds the words that
    state it. Never substituted: the original words stay where they were.
    """
    values = []
    for number in spelled_numbers(text):
        values.append(str(number.value))
        if number.value >= 1000:
            values.append(f"{number.value:,}")
    return " ".join(dict.fromkeys(values))


def with_digits(text: str) -> str:
    """The normalised text with each spelled-out number replaced by its digits.

    For comparisons that read quantities positionally — "the number after these words" —
    where appending the digits elsewhere would detach them from the words that name them.
    The result is a working copy for measurement, never text shown to anyone.
    """
    normalised = normalize(text)
    pieces: list[str] = []
    cursor = 0
    for number in spelled_numbers(normalised):
        pieces.append(normalised[cursor:number.start])
        pieces.append(str(number.value))
        cursor = number.end
    pieces.append(normalised[cursor:])
    return "".join(pieces)
