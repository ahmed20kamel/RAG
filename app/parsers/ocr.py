"""Reading pages that are pictures of text, when the machine can actually do it.

A third of the PDFs on this company's machines are scans: a contract photographed into a
file, with no text layer at all. Extraction returns nothing from them, and "nothing" is
the one honest answer available without OCR.

So OCR is optional and its absence is reported rather than worked around. Every capability
it needs — the Python binding, the Tesseract binary, and an Arabic language pack — is
checked before anything is attempted, and a missing one produces a document marked
`ocr_required` with a message naming what is missing. That refusal is the feature. A
scanned Arabic contract run through an English-only engine produces fluent-looking
nonsense, and nonsense that reaches the index is worse than a file that never did:
somebody will cite it.

Nothing here guesses at a page it could not read. An empty result stays empty.
"""

from __future__ import annotations

import io
import re
import logging
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.parsers.text_repair import repair_arabic

logger = logging.getLogger(__name__)

#: Rendering resolution. 200 was measured against real scans on this hardware: 150 lost
#: diacritics and small print, 300 roughly doubled the time for no accuracy this corpus
#: could show.
RENDER_DPI = 200

#: Arabic first — it is what these documents are — with English alongside, because the
#: same page usually carries both and dropping one loses half the reference numbers.
LANGUAGES = "ara+eng"

#: Below this, a page of OCR output is noise rather than text.
MIN_USABLE_CHARS = 20

#: Where a language pack lives when it was installed for this project rather than
#: system-wide, which needs no administrator and keeps the app in control of it.
LOCAL_TESSDATA = Path(__file__).resolve().parent.parent.parent / "data" / "tessdata"


@dataclass(frozen=True)
class OcrCapability:
    """Whether OCR can run here, and what is missing when it cannot."""

    available: bool
    reason: str = ""
    languages: tuple[str, ...] = ()
    binary: str = ""

    @property
    def has_arabic(self) -> bool:
        return "ara" in self.languages


def _tesseract_path() -> str:
    """The engine, from the environment, the PATH, or where Windows installs it."""
    configured = os.environ.get("TESSERACT_CMD", "").strip()
    if configured and Path(configured).exists():
        return configured

    found = shutil.which("tesseract")
    if found:
        return found

    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ):
        if Path(candidate).exists():
            return candidate
    return ""


@lru_cache(maxsize=1)
def capability() -> OcrCapability:
    """What this machine can do, worked out once.

    Cached because it shells out to the engine, and the answer cannot change while the
    process is running. A test that needs to re-ask calls `capability.cache_clear()`.
    """
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return OcrCapability(False, "مكتبة pytesseract غير مثبتة على الخادم.")

    binary = _tesseract_path()
    if not binary:
        return OcrCapability(
            False,
            "برنامج Tesseract غير مثبت أو غير موجود في PATH. "
            "ثبّته أو اضبط المتغير TESSERACT_CMD.",
        )

    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = binary
    if LOCAL_TESSDATA.exists() and not os.environ.get("TESSDATA_PREFIX"):
        # A project-local pack, so installing Arabic does not require touching a
        # system directory an operator may not have rights to.
        os.environ["TESSDATA_PREFIX"] = str(LOCAL_TESSDATA)

    try:
        languages = tuple(sorted(pytesseract.get_languages(config="")))
    except Exception as exc:  # noqa: BLE001 - the binding raises broadly here
        return OcrCapability(False, f"تعذر تشغيل Tesseract: {exc}", binary=binary)

    if "ara" not in languages:
        return OcrCapability(
            False,
            "حزمة اللغة العربية (ara) غير مثبتة في Tesseract. "
            "المستندات الممسوحة العربية تحتاجها، والتشغيل بالإنجليزية وحدها "
            "ينتج نصًا خاطئًا يبدو سليمًا.",
            languages=languages,
            binary=binary,
        )

    logger.info("OCR available: %s, languages=%s", binary, len(languages))
    return OcrCapability(True, "", languages, binary)


#: Below this many pixels on the longer side, a picture is enlarged before reading:
#: Tesseract misreads small type, and phone screenshots are often just that.
MIN_READ_SIDE = 1600


def read_image(image) -> str:
    """One picture read, or an empty string. Same languages and repair as a PDF page."""
    ability = capability()
    if not ability.available:
        return ""
    try:
        import pytesseract

        picture = image.convert("RGB")
        longest = max(picture.size)
        if longest < MIN_READ_SIDE:
            factor = MIN_READ_SIDE / longest
            picture = picture.resize((round(picture.width * factor), round(picture.height * factor)))
        text = pytesseract.image_to_string(picture, lang=LANGUAGES)
    except Exception:  # noqa: BLE001
        logger.exception("OCR failed on an image")
        return ""
    cleaned = repair_arabic(text).strip()
    return cleaned if len(cleaned) >= MIN_USABLE_CHARS else ""


#: A figure as printed: digits, with the separators of amounts, dates and references.
_FIGURE = re.compile(r"^\d[\d,./:-]*\d$|^\d$")
#: What a completion may be: an amount with thousands separators or decimals, or a date.
#: Bare digit runs are not — measured, the English reading turns phone numbers, stamps
#: and Arabic letters beside a figure into ones ("600" -> "600599", "2,000,000" ->
#: "992,000,000"), and a completed figure must be one a reader would recognise as such.
_COMPLETION = re.compile(
    r"^\d{1,3}(?:,\d{3})+(?:\.\d{1,3})?$|^\d+\.\d{1,3}$|^\d{1,2}/\d{1,2}/\d{4}$|^\d{4}[-/]\d{1,2}[-/]\d{1,2}$"
)
#: How far apart, in pixels at RENDER_DPI, the edges of two readings of one word may be.
_SAME_PLACE = 12


def _recover_numbers(image, text: str) -> str:
    """Figures the Arabic reading cut short, taken from a reading of the same place.

    Measured on a scanned statement of claim: «1,985.19 درهماً يومياً» — the daily delay
    penalty, set in bold Latin digits inside an Arabic line — was read «9 درهماً», and the
    answers then quoted a penalty of nine dirhams. Read for English alone, the same spot
    gives «1,985.19». So each figure of the Arabic reading is compared with what the
    English reading found in the same box, and replaced only when that is a longer,
    well-formed figure beginning or ending with it: a figure is completed, never invented
    or changed. The same box is the proof — the Arabic reading saw the whole figure (its
    box spans all of it) and wrote only part.
    """
    import pytesseract

    words = pytesseract.image_to_data(image, lang=LANGUAGES, output_type=pytesseract.Output.DICT)
    latin = pytesseract.image_to_data(image, lang="eng", output_type=pytesseract.Output.DICT)
    figures = [
        (latin["left"][i], latin["left"][i] + latin["width"][i], latin["top"][i], latin["text"][i].strip())
        for i in range(len(latin["text"]))
        if _COMPLETION.match(latin["text"][i].strip()) and _plausible_date(latin["text"][i].strip())
    ]
    for i, word in enumerate(words["text"]):
        word = word.strip()
        # A figure read whole is left as read: beside an Arabic letter the English
        # reading adds digits of its own («2,000,000» became «992,000,000»).
        if not _FIGURE.match(word) or _COMPLETION.match(word):
            continue
        left, right, top = words["left"][i], words["left"][i] + words["width"][i], words["top"][i]
        for figure_left, figure_right, figure_top, figure in figures:
            digits, part = figure.replace(",", ""), word.replace(",", "")
            if (
                abs(figure_left - left) <= _SAME_PLACE and abs(figure_right - right) <= _SAME_PLACE
                and abs(figure_top - top) <= _SAME_PLACE
                and len(digits) > len(part) and (digits.startswith(part) or digits.endswith(part))
            ):
                neighbour = _next_word(words, i)
                pattern = rf"(?<![\d,.]){re.escape(word)}(?![\d,.])" + (rf"(?=\s+{re.escape(neighbour)})" if neighbour else "")
                if len(re.findall(pattern, text)) == 1:
                    text = re.sub(pattern, figure, text)
                    logger.info("OCR figure completed: %s -> %s", word, figure)
                break
    return text


#: A percentage the Arabic reading took the sign of for digits: «%10» printed right to
#: left came out «9010», «%5» as «905» or «9/5». Measured on the scanned contracts: the
#: delay penalty, the retention and the performance bond all read as four-digit
#: numbers. Repaired only where the words around it make it a share of something —
#: «من قيمة المقاولة», «بالمائة» — so an amount such as «9010 درهم» is never touched.
_MISREAD_PERCENT = re.compile(
    r"(?<![\d,./])(?:90|9/)(\d{1,2})(?![\d,./])(\)?)(?=\s*(?:\S+\s+){0,2}?(?:من\s+(?:قيمة|المقاولة|العقد|الأعمال|مبلغ)|"
    r"بالمائة|بالمئة|في\s+المائة|في\s+المئة))"
)


def repair_percentages(text: str) -> str:
    repaired, count = _MISREAD_PERCENT.subn(lambda m: f"{m.group(1)}%{m.group(2)}", text)
    if count:
        logger.info("OCR percentages repaired: %s", count)
    return repaired


def _plausible_date(figure: str) -> bool:
    """A date with a real day and month; anything that is not a date passes («97/6/1981»
    was «7/6/1981» with a letter beside it read as a 9)."""
    parts = re.split(r"[/-]", figure)
    if len(parts) != 3:
        return True
    day, month = (parts[2], parts[1]) if len(parts[0]) == 4 else (parts[0], parts[1])
    return 1 <= int(day) <= 31 and 1 <= int(month) <= 12


def _next_word(words: dict, index: int) -> str:
    """The word after `index` on the same line, which pins down where a figure is in the text."""
    for j in range(index + 1, len(words["text"])):
        if words["line_num"][j] != words["line_num"][index] or words["block_num"][j] != words["block_num"][index]:
            return ""
        if words["text"][j].strip():
            return words["text"][j].strip()
    return ""


def read_page(page) -> str:
    """One PDF page rendered and read, or an empty string.

    Failure returns nothing rather than raising: a page that would not render should cost
    that page, not the document. The caller decides whether what came back is enough.
    """
    ability = capability()
    if not ability.available:
        return ""

    try:
        import pytesseract
        from PIL import Image

        pixmap = page.get_pixmap(dpi=RENDER_DPI)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        text = pytesseract.image_to_string(image, lang=LANGUAGES)
        text = repair_percentages(_recover_numbers(image, text))
    except Exception:  # noqa: BLE001
        logger.exception("OCR failed on page %s", getattr(page, "number", "?"))
        return ""

    # Repaired here rather than by the caller, so no path can index raw OCR output.
    # Tesseract wraps Latin runs inside Arabic text in bidi control marks, and left in
    # they sit inside words and stop a reference number matching the same number typed
    # by hand. Word order is not touched: OCR already reads in logical order.
    cleaned = repair_arabic(text).strip()
    return cleaned if len(cleaned) >= MIN_USABLE_CHARS else ""
