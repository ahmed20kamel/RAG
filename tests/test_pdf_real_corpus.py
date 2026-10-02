"""Every PDF on this machine, parsed — the corpus the parser was actually built for.

The synthetic corpus tests the properties one at a time. This runs the parser over real
files: scans, invoices, court notices, bank statements, contracts, forms. They are what
exposed each problem the parser now handles, and they are the only honest check that it
handles them at scale rather than in the one case that was looked at.

Nothing here asserts what any file says. These hold client, employee and personal data,
so the suite reports shapes and counts — pages, sections, whether OCR ran, whether the
Arabic came back in reading order — and prints no document content. What it enforces is
that every file reaches a definite outcome: parsed, or refused for a stated reason.
A crash is the failure this exists to catch.

Set PDF_CORPUS_DIR to point it elsewhere.

    python tests/test_pdf_real_corpus.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.exceptions import ExtractionError, OcrRequiredError, ParsingError, RagError  # noqa: E402
from app.parsers import ocr  # noqa: E402
from app.parsers.pdf_parser import PdfParser  # noqa: E402

CORPUS_DIR = Path(os.environ.get("PDF_CORPUS_DIR", str(Path.home() / "Desktop")))

PRESENTATION_FORMS = re.compile(r"[ﭐ-﷿ﹰ-﻿]")
BIDI = re.compile(r"[‎‏‪-‮]")
ARABIC = re.compile(r"[؀-ۿ]")

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def main() -> None:
    files = sorted(CORPUS_DIR.glob("*.pdf"))
    if not files:
        print(f"no PDFs under {CORPUS_DIR}", file=sys.stderr)
        raise SystemExit(2)

    ability = ocr.capability()
    print(f"OCR: {'available' if ability.available else ability.reason}")
    print(f"{len(files)} real PDF(s) under {CORPUS_DIR}\n")

    parser = PdfParser()
    parsed, refused, crashed = [], [], []
    ocr_used = 0
    started_all = time.perf_counter()

    print(f"{'file':40}{'pages':>6}{'sections':>9}{'ocr':>5}{'secs':>7}  outcome")
    for path in files:
        name = path.name[:38]
        started = time.perf_counter()
        try:
            document = parser.parse(path.read_bytes(), path.name)
        except (OcrRequiredError, ExtractionError, ParsingError) as exc:
            elapsed = time.perf_counter() - started
            refused.append((path.name, type(exc).__name__))
            print(f"{name:40}{'—':>6}{'—':>9}{'—':>5}{elapsed:>7.1f}  refused: {type(exc).__name__}")
            continue
        except RagError as exc:
            refused.append((path.name, type(exc).__name__))
            print(f"{name:40}{'—':>6}{'—':>9}{'—':>5}{'—':>7}  refused: {type(exc).__name__}")
            continue
        except Exception as exc:  # noqa: BLE001 - this is what the suite is for
            crashed.append((path.name, f"{type(exc).__name__}: {exc}"))
            print(f"{name:40}{'—':>6}{'—':>9}{'—':>5}{'—':>7}  CRASHED: {type(exc).__name__}")
            continue

        elapsed = time.perf_counter() - started
        pages = int(document.metadata.get("pages", 0))
        used = int(document.metadata.get("ocr_pages", 0))
        ocr_used += 1 if used else 0
        parsed.append((path.name, document))
        print(
            f"{name:40}{pages:>6}{len(document.sections):>9}{used:>5}{elapsed:>7.1f}  ok"
        )

    total = time.perf_counter() - started_all
    print(f"\nparsed {len(parsed)}, refused {len(refused)}, crashed {len(crashed)} "
          f"in {total:.0f}s\n")

    # -- what must hold across the whole corpus --------------------------
    print("-- 1. every file reaches a definite outcome --")
    check(not crashed, f"nothing crashed ({[c[0] for c in crashed]})")
    check(len(parsed) + len(refused) == len(files), "each file parsed or was refused")

    print("\n-- 2. refusals name a reason a person can act on --")
    kinds = {kind for _, kind in refused}
    check(
        kinds <= {"OcrRequiredError", "ExtractionError", "ParsingError"},
        f"every refusal is one of the three stated kinds ({kinds or 'none'})",
    )

    print("\n-- 3. the text that came back is repaired --")
    joined = "\n".join(d.raw_text for _, d in parsed)
    check(
        not PRESENTATION_FORMS.search(joined),
        "no Arabic presentation form survived anywhere in the corpus",
    )
    check(not BIDI.search(joined), "no bidi control mark survived either")
    check("­" not in joined, "and no soft hyphen")

    print("\n-- 4. structure and locators --")
    check(
        all(d.sections for _, d in parsed),
        "every parsed document produced at least one section",
    )
    located = [
        s for _, d in parsed for s in d.sections if s.locator
    ]
    check(
        len(located) == sum(len(d.sections) for _, d in parsed),
        f"every section carries a locator ({len(located)})",
    )
    check(
        all(s.locator.startswith("صفحة") for s in located),
        "and every one of them names a page",
    )

    print("\n-- 5. page numbers are within the document --")
    out_of_range = [
        (name, s.location.page, int(d.metadata.get("pages", 0)))
        for name, d in parsed
        for s in d.sections
        if s.location and s.location.page
        and not 1 <= s.location.page <= int(d.metadata.get("pages", 0))
    ]
    check(not out_of_range, f"no section cites a page the file does not have ({out_of_range[:3]})")

    print("\n-- 6. scanned files were read rather than silently emptied --")
    if ability.available:
        check(
            ocr_used > 0,
            f"OCR ran on {ocr_used} file(s) — the scans in this corpus were read",
        )
        check(
            all(d.extraction_warning for _, d in parsed if d.metadata.get("ocr_pages")),
            "and every OCR'd document carries the warning that it was",
        )
    else:
        check(
            all(kind == "OcrRequiredError" for _, kind in refused) or not refused,
            "without OCR, scans are refused as ocr_required rather than indexed empty",
        )

    print("\n-- 7. Arabic documents produced Arabic --")
    arabic_docs = [(n, d) for n, d in parsed if ARABIC.search(d.raw_text)]
    check(bool(arabic_docs), f"the corpus contains Arabic documents ({len(arabic_docs)})")
    check(
        all(d.language in ("ar", "mixed", "en") for _, d in parsed),
        "and every document got a language",
    )


if __name__ == "__main__":
    main()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("The real PDF corpus parses without a crash, and every file lands somewhere definite.")
