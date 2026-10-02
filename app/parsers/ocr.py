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
    except Exception:  # noqa: BLE001
        logger.exception("OCR failed on page %s", getattr(page, "number", "?"))
        return ""

    # Repaired here rather than by the caller, so no path can index raw OCR output.
    # Tesseract wraps Latin runs inside Arabic text in bidi control marks, and left in
    # they sit inside words and stop a reference number matching the same number typed
    # by hand. Word order is not touched: OCR already reads in logical order.
    cleaned = repair_arabic(text).strip()
    return cleaned if len(cleaned) >= MIN_USABLE_CHARS else ""
