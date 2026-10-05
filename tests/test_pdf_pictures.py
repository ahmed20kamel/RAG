"""A table pasted into a PDF as a picture is read; a letterhead printed as one is not.

Found live: a variation order's added-items rates were a picture under a typed heading.
The page had typed text, so it was never read by OCR, and every question about the rates
was answered "not in the sources". Pages like that are now read — the pictures on them,
not the rendered page — and only lines bringing figures the typed text lacks are added.
A letterhead printed as a full-page background, the common case, adds nothing: reading
it would put addresses and misread copies of the page's own numbers into the index.

Offline; needs Tesseract. Run: python tests/test_pdf_pictures.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from fpdf import FPDF  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.parsers.ocr import capability  # noqa: E402
from app.parsers.pdf_parser import PdfParser  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def picture(rows: list[tuple[str, ...]], size=(1600, 700), columns=(60, 260, 1250)) -> Path:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for i, row in enumerate(rows):
        for x, cell in zip(columns, row):
            draw.text((x, 60 + i * 140), cell, fill="black", font=font(40))
    path = Path(tempfile.mkdtemp()) / "picture.png"
    image.save(path)
    return path


def main() -> int:
    if not capability().available:
        print("[SKIP] OCR is not installed here")
        return 0

    print("=== 1. a table pasted as a picture under a typed heading ===")
    table = picture([("Item", "Description", "Rate AED"), ("1", "Interior wall paint - Jotun", "18.50"),
                     ("2", "Interior ceiling paint", "16.75"), ("3", "Exterior paint Jotashield", "24.90")])
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.cell(0, 10, "VARIATION ORDER VAR0020 - ADDED ITEMS - villa project, internal works")
    pdf.image(str(table), x=10, y=30, w=190)
    parsed = PdfParser().parse(bytes(pdf.output()), "vo.pdf")
    check(all(rate in parsed.raw_text for rate in ("18.50", "16.75", "24.90")),
          "the rates in the picture reach the text", parsed.raw_text[-200:])
    check("VARIATION ORDER" in parsed.raw_text, "the typed heading is kept")

    print("\n=== 2. a letterhead printed as a full-page background ===")
    letterhead = picture([("FUTURE BUILD ENGINEERING CONSULTANCY LLC", "", ""),
                          ("Tel: +971 2 626 4175", "", ""), ("P.O.Box: 28120, Abu Dhabi, UAE", "", "")],
                         size=(1240, 1754), columns=(80, 0, 0))
    pdf = FPDF()
    pdf.add_page()
    pdf.image(str(letterhead), x=0, y=0, w=210, h=297)
    pdf.set_font("helvetica", size=12)
    pdf.set_xy(20, 120)
    pdf.multi_cell(170, 8, "Subject: progress approval. The certified progress is 90% as of 10/02/2026 "
                           "and the payment of AED 125,000 is due within 45 days.")
    data = bytes(pdf.output())
    with_pictures = PdfParser().parse(data, "letter.pdf").raw_text
    typed_only = PdfParser(enable_ocr=False).parse(data, "letter.pdf").raw_text
    check(with_pictures == typed_only, "nothing is added: the letterhead's addresses stay out of the index",
          with_pictures[len(typed_only):][:200])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
