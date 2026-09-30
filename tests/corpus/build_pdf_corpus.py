"""PDFs built to have the properties that make PDFs hard.

The real files on this machine are the reference — a scanned 21-page contract, an invoice
whose Arabic extracts backwards, a court notice stored as presentation glyphs — but they
hold client and personal data and cannot be a fixture. These reproduce the same
properties from scratch:

* text with headings set larger than the body, so heading detection has something to find;
* Arabic that must survive extraction intact;
* a page rendered as an image with no text layer, which is the OCR case;
* a table;
* a file that is not a PDF at all.

Built with PyMuPDF so no fonts have to be shipped; the Arabic is drawn with a font the
library resolves itself.

    python tests/corpus/build_pdf_corpus.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")

BODY = 11
HEADING = 18
SUBHEADING = 14

FONT_DIR = next(
    (d for d in ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts")
     if Path(d).exists()),
    "",
)
FONT_FILE = next(
    (f for f in ("arial.ttf", "tahoma.ttf", "DejaVuSans.ttf", "Arial Unicode.ttf")
     if FONT_DIR and (Path(FONT_DIR) / f).exists()),
    "",
)

CSS = f"@font-face{{font-family:ar;src:url({FONT_FILE});}} *{{font-family:ar;}}"


def _page(document, lines: list[tuple[str, int]]) -> None:
    """One page of Arabic, laid out right to left at the sizes given.

    Written through the HTML box rather than by placing words at coordinates. Placing
    them by hand produced a file whose words extracted with their letters reversed —
    the corpus was testing the builder, not the parser. The HTML path does the
    bidirectional layout itself, and the font is named explicitly because the default
    substitutes glyphs it does not have and the Arabic comes back as mojibake.
    """
    page = document.new_page()
    html = "".join(
        f'<p style="font-size:{size}px;margin:10px 0">{text}</p>' for text, size in lines
    )
    page.insert_htmlbox(
        pymupdf.Rect(40, 40, 555, 780),
        f'<div dir="rtl">{html}</div>',
        css=CSS,
        archive=pymupdf.Archive(FONT_DIR) if FONT_DIR else None,
    )


def structured() -> pymupdf.Document:
    """Headings at two sizes over three pages, with values to retrieve."""
    document = pymupdf.open()
    _page(
        document,
        [
            ("تقرير المشروع السنوي", HEADING),
            ("القسم الأول: نطاق الأعمال", SUBHEADING),
            ("يشمل نطاق الأعمال أعمال الحفر والأساسات والهيكل الخرساني.", BODY),
            ("بلغت المساحة الإجمالية المعتمدة 4,250 مترًا مربعًا.", BODY),
        ],
    )
    _page(
        document,
        [
            ("القسم الثاني: الجدول الزمني", SUBHEADING),
            ("تبدأ الأعمال في 15/03/2026 وتنتهي خلال 240 يومًا تقويميًا.", BODY),
            ("رقم أمر المباشرة هو CMN-2026-0817.", BODY),
        ],
    )
    _page(
        document,
        [
            ("القسم الثالث: الضمانات", SUBHEADING),
            ("مدة ضمان الأعمال الإنشائية عشر سنوات من تاريخ الاستلام الابتدائي.", BODY),
            ("قيمة ضمان حسن التنفيذ 50,000 درهم.", BODY),
        ],
    )
    return document


def flat() -> pymupdf.Document:
    """One size throughout — no headings to find, so pages become the sections."""
    document = pymupdf.open()
    for number in range(1, 4):
        _page(
            document,
            [
                (f"هذه الصفحة رقم {number} من مستند بلا عناوين مميزة.", BODY),
                (f"القيمة المرجعية الخاصة بهذه الصفحة هي REF-{number:03d}-VAL.", BODY),
                ("يستمر النص بالحجم نفسه دون أي تمييز بصري للعناوين.", BODY),
            ],
        )
    return document


def with_table() -> pymupdf.Document:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_htmlbox(
        pymupdf.Rect(40, 40, 555, 780),
        """
        <div dir="rtl">
        <p style="font-size:18px">جدول الدفعات</p>
        <table border="1" cellpadding="6" style="font-size:12px;border-collapse:collapse">
          <tr><td>الدفعة</td><td>النسبة</td><td>المبلغ</td></tr>
          <tr><td>الأولى</td><td>30%</td><td>804000</td></tr>
          <tr><td>الثانية</td><td>45%</td><td>1206000</td></tr>
          <tr><td>النهائية</td><td>25%</td><td>670000</td></tr>
        </table>
        </div>
        """,
        css=CSS,
        archive=pymupdf.Archive(FONT_DIR) if FONT_DIR else None,
    )
    return document


def ruled_table() -> pymupdf.Document:
    """A table drawn with real ruled lines, which is what PDF table detection needs.

    The HTML-rendered table in `with_table.pdf` has borders that never become vector
    lines in the file, and detection finds nothing in it — which is the honest common
    case and is tested as such. This one is drawn with actual lines, so the path that
    produces a structured grid is exercised too.
    """
    document = pymupdf.open()
    page = document.new_page()
    rows = [
        ["Item", "Unit", "Qty", "Rate", "Amount"],
        ["Excavation", "m3", "1200", "55.36", "66432"],
        ["Backfill", "m3", "860", "43.29", "37229"],
        ["Concrete", "m3", "340", "210.00", "71400"],
        ["Reinforcement", "ton", "96", "2450.00", "235200"],
    ]
    left, top, row_height = 60.0, 80.0, 26.0
    widths = [130.0, 55.0, 60.0, 75.0, 90.0]
    right = left + sum(widths)

    page.insert_text((left, top - 18), "Bill of Quantities", fontsize=16, fontname="hebo")

    for index in range(len(rows) + 1):
        y = top + index * row_height
        page.draw_line(pymupdf.Point(left, y), pymupdf.Point(right, y))
    x = left
    for width in [0.0, *widths]:
        x += width
        page.draw_line(
            pymupdf.Point(x, top), pymupdf.Point(x, top + len(rows) * row_height)
        )

    for r, values in enumerate(rows):
        x = left + 5
        for c, value in enumerate(values):
            page.insert_text(
                (x, top + r * row_height + 17),
                value,
                fontsize=10,
                fontname="hebo" if r == 0 else "helv",
            )
            x += widths[c]
    return document


def scanned() -> pymupdf.Document:
    """Pages rendered to images, so there is no text layer at all — the OCR case."""
    source = structured()
    document = pymupdf.open()
    for page in source:
        # 110 DPI keeps the file inside the upload limit while staying legible enough
        # for OCR; 200 produced a 34 MB fixture the API correctly refused.
        pixmap = page.get_pixmap(dpi=110)
        target = document.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, pixmap=pixmap)
    source.close()
    return document


def mixed_scan() -> pymupdf.Document:
    """A readable document with one image page bound into it, as contracts often are."""
    document = structured()
    # The page object is invalidated by delete_page, so everything needed from it is
    # taken first — reading `page.rect` afterwards raises inside PyMuPDF.
    page = document[1]
    pixmap = page.get_pixmap(dpi=110)
    width, height = page.rect.width, page.rect.height
    document.delete_page(1)
    target = document.new_page(1, width=width, height=height)
    target.insert_image(target.rect, pixmap=pixmap)
    return document


def empty_pdf() -> pymupdf.Document:
    document = pymupdf.open()
    document.new_page()
    return document


BUILDERS = {
    "structured.pdf": structured,
    "flat.pdf": flat,
    "with_table.pdf": with_table,
    "ruled_table.pdf": ruled_table,
    "scanned.pdf": scanned,
    "mixed_scan.pdf": mixed_scan,
    "empty.pdf": empty_pdf,
}


def build() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    for name, builder in BUILDERS.items():
        document = builder()
        document.save(HERE / name)
        document.close()
        print(f"  {name:22} {(HERE / name).stat().st_size:>9,} bytes")

    (HERE / "corrupt.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 300)
    (HERE / "renamed_docx.pdf").write_bytes((HERE / "long_document.docx").read_bytes())
    print("  corrupt.pdf / renamed_docx.pdf  (rejection cases)")


if __name__ == "__main__":
    print("building the PDF corpus\n")
    build()
    print(f"\nwritten to {HERE}")
