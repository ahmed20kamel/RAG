"""Arabic-aware text normalisation and tokenisation.

Shared by keyword search, entity extraction and query analysis so that a term
written slightly differently in the question still matches the document.
Identifier-like tokens (contract numbers, licence numbers, dates) are preserved
whole *and* split, because those are exactly the terms vector search misses.
"""

from __future__ import annotations

import re
import unicodedata

ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
DIACRITICS = re.compile(r"[ً-ْٓ-ٰٟـ]")
# Arabic punctuation sits inside the Arabic Unicode block, so \w-style classes treat
# it as part of a word and produce junk terms like "الإجمالية؟". Split on it explicitly.
ARABIC_PUNCTUATION = "،؍؛؞؟٪٬٭۔۝"
NON_WORD = re.compile(rf"[^\w؀-ۿ]+|[{ARABIC_PUNCTUATION}]+", re.UNICODE)
IDENTIFIER = re.compile(r"[A-Za-z0-9]+(?:[-/._][A-Za-z0-9]+)+")
DATE_LIKE = re.compile(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{2,4}\b")
NUMBER_LIKE = re.compile(r"\d[\d,]*\.?\d*%?")
INVISIBLE = re.compile(r"[​-‏‪-‮﻿]")

ARABIC_STOPWORDS = frozenset("""
من في على الى الي عن مع هذا هذه ذلك تلك التي الذي الذين ما لا و او أو ثم قد كان كانت
يكون تكون هو هي هم هن انا نحن انت انتم كل بعض غير بين عند لدى حتى اذا إذا لكن لأن لان
بعد قبل حول ضد دون سوى مثل هناك هنالك ايضا أيضا كما لقد منذ خلال عبر نحو ولا وما وهو
وهي التى اللتي اي أي ان أن إن الا إلا به بها له لها فيه فيها عليه عليها منه منها
""".split())

ENGLISH_STOPWORDS = frozenset("""
the a an and or of to in for on at by is are was were be been with from as that this
these those it its if then than but not no nor so such which who whom what when where
how all any each both few more most other some only own same too very can will just
""".split())

STOPWORDS = ARABIC_STOPWORDS | ENGLISH_STOPWORDS

ARABIC_RANGE = re.compile(r"[؀-ۿݐ-ݿ]")
LATIN_RANGE = re.compile(r"[A-Za-z]")


def normalize(text: str) -> str:
    """Fold the spelling variants that would otherwise break an exact match."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = INVISIBLE.sub("", text)
    text = text.translate(ARABIC_INDIC)
    text = DIACRITICS.sub("", text)
    text = (
        text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
        .replace("ى", "ي").replace("ئ", "ي").replace("ة", "ه").replace("ؤ", "و")
    )
    text = re.sub(rf"[{ARABIC_PUNCTUATION}]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


# Light Arabic stemming: the definite-article family plus a few plural/feminine
# endings. Applied to both documents and queries, so "السيناريوهات" in a document
# matches "سيناريوهات" in a question. Single-letter clitics are deliberately left
# alone because stripping them mangles names such as "وضاح".
AR_PREFIXES = ("وال", "فال", "بال", "كال", "لل", "ال")
AR_SUFFIXES = ("اتها", "اتهم", "ياته", "ات", "ون", "ين", "يه", "ها", "هم", "كم", "نا")
MIN_STEM_LENGTH = 3


def stem(token: str) -> str:
    if not ARABIC_RANGE.search(token):
        return token
    for prefix in AR_PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= MIN_STEM_LENGTH:
            token = token[len(prefix):]
            break
    for suffix in AR_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= MIN_STEM_LENGTH:
            token = token[: -len(suffix)]
            break
    return token


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Split into search terms, emitting identifiers whole and in parts."""
    normalised = normalize(text)
    tokens: list[str] = []

    for identifier in IDENTIFIER.findall(normalised):
        tokens.append(identifier)
        tokens.extend(part for part in re.split(r"[-/._]", identifier) if len(part) > 1)

    for raw in NON_WORD.split(normalised):
        if not raw:
            continue
        stripped = raw.strip("_")
        if len(stripped) < 2 and not stripped.isdigit():
            continue
        if not keep_stopwords and stripped in STOPWORDS:
            continue
        tokens.append(stem(stripped))

    return tokens


def content_terms(text: str) -> set[str]:
    """Distinct meaningful terms, used for overlap scoring."""
    return {token for token in tokenize(text) if len(token) > 2 or token.isdigit()}


def detect_language(text: str) -> str:
    arabic = len(ARABIC_RANGE.findall(text))
    latin = len(LATIN_RANGE.findall(text))
    total = arabic + latin
    if total == 0:
        return "unknown"
    ratio = arabic / total
    if ratio >= 0.6:
        return "ar"
    if ratio <= 0.15:
        return "en"
    return "mixed"


def numeric_tokens(text: str) -> set[str]:
    """Numbers, dates and identifiers — the values an answer must not invent."""
    normalised = normalize(text)
    values = set(DATE_LIKE.findall(normalised))
    values |= set(IDENTIFIER.findall(normalised))
    for match in NUMBER_LIKE.findall(normalised):
        cleaned = match.strip(".,")
        if cleaned:
            values.add(cleaned)
    return values


DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
YMD = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")


def parse_date(text: str) -> tuple[int, int, int] | None:
    """First day/month/year found in the text, as a sortable tuple."""
    normalised = normalize(text)
    match = DMY.search(normalised)
    if match:
        day, month, year = (int(g) for g in match.groups())
    else:
        match = YMD.search(normalised)
        if not match:
            return None
        year, month, day = (int(g) for g in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def extract_dates(text: str) -> list[str]:
    """Every day/month/year date in the text, in the order written."""
    return [m.group(0) for m in DMY.finditer(normalize(text))]


MONTH_NAMES = {
    **{name: number for number, name in enumerate(
        ("يناير", "فبراير", "مارس", "ابريل", "مايو", "يونيو",
         "يوليو", "اغسطس", "سبتمبر", "اكتوبر", "نوفمبر", "ديسمبر"), start=1)},
    **{name: number for number, name in enumerate(
        ("january", "february", "march", "april", "may", "june",
         "july", "august", "september", "october", "november", "december"), start=1)},
}
WRITTEN_DATE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(MONTH_NAMES) + r")\s+(\d{4})\b"
)


def all_dates(text: str) -> set[tuple[int, int, int]]:
    """Every date the text states, however it spells it.

    "10/02/2026" and "10 فبراير 2026" are the same day, so a check that only reads
    digits would call a correctly stated date missing.
    """
    normalised = normalize(text)
    found = {
        parsed
        for match in DMY.finditer(normalised)
        if (parsed := parse_date(match.group(0)))
    }
    for day, month, year in WRITTEN_DATE.findall(normalised):
        number = MONTH_NAMES.get(month)
        if number and 1 <= int(day) <= 31:
            found.add((int(year), number, int(day)))
    return found


def strip_thousands(value: str) -> str:
    return value.replace(",", "").replace(" ", "")
