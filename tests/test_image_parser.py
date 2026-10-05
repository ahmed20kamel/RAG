"""Pictures are read like scanned pages: text out, page by page, or a clear refusal.

Builds images on the fly — a photographed-letter stand-in with a reference number and an
amount, a two-page TIFF, a blank photo — and checks that the text and the numbers come
through, that pages are cited by page, that a picture with no text is refused with that
reason, and that the type check knows a picture from a renamed PDF.

Needs the Tesseract engine (skips with a note when it is missing). The text is invented.

Run: python tests/test_image_parser.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.core.domain import FileType  # noqa: E402
from app.exceptions import ParsingError  # noqa: E402
from app.parsers import ocr  # noqa: E402
from app.parsers.detection import detect  # noqa: E402
from app.parsers.image_parser import ImageParser  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def page(lines: list[str], size=(1400, 700)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 80), line, fill="black", font=font(44))
    return image


def encode(image: Image.Image, fmt: str, **kwargs) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def main() -> int:
    print("\n=== 1. the type check knows pictures ===")
    letter = page(["Reference: LTR-2026-0417", "Delay penalty: 1,850 AED per day", "Site handover: 15 November 2026"])
    png, jpg = encode(letter, "PNG"), encode(letter, "JPEG", quality=92)
    check(detect(png, ".png").agrees and detect(jpg, ".jpg").agrees, "PNG and JPEG are recognised by their bytes")
    check(not detect(b"%PDF-1.7 ...", ".png").agrees, "a PDF renamed to .png is caught")
    check(detect(png, ".png").file_type == FileType.IMAGE, "and reported as an image")

    if not ocr.capability().available:
        print(f"\n(skipping OCR checks: {ocr.capability().reason})")
        return 0 if not FAILURES else 1

    print("\n=== 2. a photographed letter becomes text ===")
    parsed = ImageParser().parse(jpg, "letter-photo.jpg")
    text = parsed.raw_text
    check("LTR-2026-0417" in text.replace(" ", ""), "the reference number comes through", text[:200])
    check("1,850" in text or "1850" in text, "the amount comes through", text[:200])
    check(parsed.file_type == FileType.IMAGE and parsed.metadata.get("ocr_pages") == 1, "recorded as one read page")
    check(parsed.sections[0].location.page == 1, "cited as page 1")

    print("\n=== 3. a small screenshot is enlarged before reading ===")
    small = letter.resize((560, 280))
    check("1,850" in ImageParser().parse(encode(small, "PNG"), "screenshot.png").raw_text.replace("1850", "1,850"),
          "small type still reads")

    print("\n=== 4. a multi-page TIFF is read page by page ===")
    second = page(["Payment certificate No. 7", "Amount due: 96,400 AED"])
    tiff = io.BytesIO()
    letter.save(tiff, format="TIFF", save_all=True, append_images=[second])
    multi = ImageParser().parse(tiff.getvalue(), "scan.tiff")
    check(len(multi.sections) == 2, "two pages, two sections", str(len(multi.sections)))
    check(multi.sections[1].location.page == 2 and "96,400" in multi.sections[1].content.replace("96400", "96,400"),
          "the second page is cited as page 2")

    print("\n=== 5. a picture without text is refused, with the reason ===")
    try:
        ImageParser().parse(encode(Image.new("RGB", (1200, 800), (90, 140, 60)), "PNG"), "site-photo.png")
        check(False, "a blank photo is refused")
    except ParsingError as exc:
        check("نص مقروء" in exc.message, "a photo with no text is refused as having no readable text")
    try:
        ImageParser().parse(b"not an image at all", "broken.png")
        check(False, "a broken file is refused")
    except ParsingError as exc:
        check("تالف" in exc.message, "a broken file is refused as damaged")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
