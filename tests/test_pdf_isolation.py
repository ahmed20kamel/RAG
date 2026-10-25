"""PDFs are read in worker processes, and a reader crash costs a file, not the server.

PyMuPDF may not be used by two threads at once. On Rag-pc, six files re-read together
crashed the whole server inside mupdfcpp64.dll. These checks hold the replacement to:

* a file read in a worker process comes out exactly as it does read in place;
* several files read at once all come out right;
* after a worker dies, the next file is still read (in a fresh pool).

    python tests/test_pdf_isolation.py
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def sample_pdf(marker: str) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for number in range(1, 4):
        page = document.new_page()
        page.insert_text((72, 72), f"Delay penalty schedule {marker}", fontsize=18)
        page.insert_text((72, 120), f"Page {number} of file {marker}: the daily rate is 1,985.19 AED from 14/06/2026.", fontsize=11)
    data = document.tobytes()
    document.close()
    return data


def sections(parsed) -> list[str]:
    return [section.content for section in parsed.sections]


def main() -> int:
    from app.parsers import pdf_parser
    from app.parsers.pdf_parser import PdfParser

    content = sample_pdf("A")
    here = PdfParser(enable_ocr=False).parse(content, "a.pdf")
    away = PdfParser(enable_ocr=False, processes=2).parse(content, "a.pdf")
    check(sections(here) == sections(away) and here.title == away.title,
          "a file read in a worker process comes out as read in place")

    files = {name: sample_pdf(name) for name in "BCDEFG"}
    reader = PdfParser(enable_ocr=False, processes=3)
    with ThreadPoolExecutor(max_workers=6) as threads:
        results = dict(zip(files, threads.map(lambda name: reader.parse(files[name], f"{name}.pdf"), files)))
    check(all(f"of file {name}:" in sections(results[name])[0] for name in files),
          "six files read at once all come out right, each with its own text")

    # A worker that dies the way a library crash kills it.
    pdf_parser._reader_pool(2).submit(os._exit, 3)
    try:
        after = reader.parse(sample_pdf("H"), "h.pdf")
        check("of file H:" in sections(after)[0], "after a worker dies, the next file is still read")
    except Exception as exc:  # noqa: BLE001
        check(False, "after a worker dies, the next file is still read", repr(exc))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
