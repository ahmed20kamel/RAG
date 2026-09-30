"""Workbooks built on purpose, so a test can assert what is in them.

The company's own spreadsheets were read while designing the parser and they are what
taught it about stray notes beside a header, `#REF!` left by a broken formula, and Arabic
stored as presentation forms. They are not used as fixtures: they hold real supplier and
employee data, and a test corpus has to be something anyone can regenerate and inspect.

So these are written to match the shapes those files actually have — including the messy
ones, because a corpus of tidy workbooks would prove only that the tidy case works.

    python tests/corpus/build_xlsx_corpus.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")


def financial_summary() -> openpyxl.Workbook:
    """Several sheets, a real header two rows down, money, dates and VAT.

    The question this workbook exists to answer is "what was the total VAT for project
    X", which needs the column name and the row to survive together all the way into a
    citation.
    """
    wb = openpyxl.Workbook()

    summary = wb.active
    summary.title = "Summary"
    summary["A1"] = "الملخص المالي للمشاريع"
    summary["A2"] = "الفترة: يناير — يونيو 2026"
    summary.append([])
    summary.append(["المشروع", "قيمة العقد", "المصروف", "ضريبة القيمة المضافة", "التاريخ"])
    rows = [
        ("مشروع أوريون", 1450000, 1840000, 92000, date(2026, 3, 14)),
        ("مشروع دلتا", 1450000, 1210000, 60500, date(2026, 4, 2)),
        ("مشروع نيبتون", 3920000, 2015000, 100750, date(2026, 5, 21)),
        ("مشروع فيغا", 875000, 640000, 32000, date(2026, 6, 9)),
    ]
    for row in rows:
        summary.append(list(row))

    details = wb.create_sheet("Cost Details")
    details["A1"] = "تفاصيل التكاليف"
    details.append([])
    details.append(["البند", "الكمية", "سعر الوحدة", "الإجمالي"])
    for item in [
        ("حفر وردم", 1200, 55.36, 66432),
        ("خرسانة عادية", 340, 210.00, 71400),
        ("حديد تسليح", 96, 2450.00, 235200),
        ("أعمال كهربائية", 1, 88000.00, 88000),
    ]:
        details.append(list(item))

    arabic = wb.create_sheet("ملخص المطالبات")
    arabic.append(["رقم المطالبة", "الجهة", "المبلغ المطالب به", "الحالة"])
    for claim in [
        ("CL-2026-001", "لجنة العمليات", 48500, "قيد المراجعة"),
        ("CL-2026-002", "إدارة المشاريع", 127300, "معتمدة"),
        ("CL-2026-003", "الإدارة القانونية", 96000, "مرفوضة"),
    ]:
        arabic.append(list(claim))

    return wb


def messy_workbook() -> openpyxl.Workbook:
    """The shapes the real files had: a note beside the header, errors, two tables."""
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "BOQ"
    sheet["A1"] = "Bill of Quantities"
    sheet["A2"] = "فيلا السيد: أحمد الشامسي"
    # A header row with somebody's annotation typed into the cells beside it.
    sheet.append([])
    sheet.append(["No", "Item", "Unit", "Qty", "Price", "Total", "ADD", 0.1, "for all concrete"])
    sheet.append([1, "PRELIMINARIES", "LS", 1, 15000, 15000])
    sheet.append([2, "EXCAVATION", "m3", 1200, 55.36, 66432])
    # What a broken formula leaves behind.
    sheet.append([3, "BACKFILL", "m3", "#REF!", "#REF!", "#REF!"])
    sheet.append([4, "CONCRETE", "m3", 340, 210, 71400])

    # Two blank rows, then a separate table in the same sheet.
    sheet.append([])
    sheet.append([])
    sheet.append(["الوصف", "القيمة"])
    sheet.append(["اسم المقاول من الباطن", "شركة الأفق للمقاولات"])
    sheet.append(["فئة العمل", "حدادة ونجارة"])
    sheet.append(["قيمة العقد", 412000])

    return wb


def presentation_forms() -> openpyxl.Workbook:
    """Arabic stored as the glyphs a renderer produced, exactly as a supplier file had it."""
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "العقد"
    sheet.append(["ﺍﻟﺒﻨﺪ", "ﺍﻟﻘﻴﻤﺔ"])
    sheet.append(["ﺍﺳﻢ ﺍﻟﻤﻘﺎﻭﻝ", "ﺵﺭﻙﺓ ﺍﻝﻡﻕﺍﻭﻝﺍﺕ ﺍﻝﻭﻁﻥﻱﺓ"])
    sheet.append(["ﺍﻝﺝﻩﺓ ﺍﻝﻁﺍﻝﺏﺓ", "ﻕﺽﻱﺓ ﺭﻕــﻡ 4471"])
    return wb


def large_workbook() -> openpyxl.Workbook:
    """Long enough to split into many blocks, so row numbering is exercised."""
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Ledger"
    sheet.append(["رقم القيد", "الوصف", "مدين", "دائن"])
    for i in range(1, 501):
        sheet.append([f"JV-{i:04d}", f"قيد رقم {i}", i * 125, i * 118])
    return wb


def empty_workbook() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    return wb


def errors_only() -> openpyxl.Workbook:
    """Readable, but every cell is a broken reference. There is nothing to index."""
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Broken"
    for _ in range(5):
        sheet.append(["#REF!", "#DIV/0!", "#VALUE!"])
    return wb


BUILDERS = {
    "financial_summary.xlsx": financial_summary,
    "messy_boq.xlsx": messy_workbook,
    "presentation_forms.xlsx": presentation_forms,
    "large_ledger.xlsx": large_workbook,
    "empty.xlsx": empty_workbook,
    "errors_only.xlsx": errors_only,
}


def build() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    for name, builder in BUILDERS.items():
        path = HERE / name
        workbook = builder()
        workbook.save(path)
        workbook.close()
        print(f"  {name:28} {path.stat().st_size:>8,} bytes")

    # Two files that are not workbooks at all, for the rejection paths.
    (HERE / "corrupt.xlsx").write_bytes(b"PK\x03\x04" + b"\x00" * 200)
    (HERE / "renamed_pdf.xlsx").write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"0" * 200)
    print("  corrupt.xlsx / renamed_pdf.xlsx  (rejection cases)")


if __name__ == "__main__":
    print("building the XLSX corpus\n")
    build()
    print(f"\nwritten to {HERE}")
