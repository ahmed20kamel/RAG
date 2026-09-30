"""PDFs, read for text, structure, tables and pages — and refused when they are pictures.

A PDF is the hardest of the four formats because it records appearance, not meaning.
There are no headings in the file, only text somebody set larger; no sections, only pages;
and no guarantee the Arabic is even in reading order. Each of those is handled explicitly
here.

The page number is the payoff. It is the locator a reader can actually act on: given
"صفحة 12" they open the file at page 12 and see the sentence the answer quoted. So it is
recorded per section and carried unchanged into every citation.

The refusal matters as much as the reading. A third of the PDFs here are scans, and a
scan with no OCR available is marked `ocr_required` rather than indexed as an empty
document that answers nothing and looks fine in the library.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter

from app.core.domain import (
    Block,
    BlockType,
    FileType,
    ParsedDocument,
    Section,
    SourceLocation,
)
from app.core.text import detect_language
from app.exceptions import ExtractionError, OcrRequiredError, ParsingError
from app.parsers import arabic_pdf, layout_tables, ocr, tables
from app.parsers.base import DocumentParser

logger = logging.getLogger(__name__)

#: Below this many characters, a page carries no text layer worth having.
MIN_PAGE_CHARS = 40

#: Above this share of empty pages, the document is a scan rather than a PDF with a
#: couple of image pages in it.
SCANNED_SHARE = 0.6

#: A heading is set larger than the body. Below this ratio the difference is leading or
#: a slightly larger label, not a structural level.
HEADING_SIZE_RATIO = 1.15

MAX_HEADING_CHARS = 120
SENTENCE_END = ("۔", ".", "!", "؟", "?", ":", "،", ",", ";", "؛")

#: A document with more headings than this per page is reading its own body text as
#: structure; better to keep pages as sections than to shred it.
MAX_HEADINGS_PER_PAGE = 6


class PdfParser(DocumentParser):
    name = "pdf"
    supported_extensions = (".pdf",)

    def __init__(self, enable_ocr: bool = True) -> None:
        self.enable_ocr = enable_ocr

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ParsingError("مكتبة قراءة PDF غير مثبتة على الخادم.") from exc

        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as exc:  # noqa: BLE001 - pymupdf raises broadly
            raise ParsingError(
                f"تعذر فتح الملف '{filename}'. قد يكون تالفًا أو محميًا بكلمة مرور."
            ) from exc

        if document.needs_pass:
            document.close()
            raise ParsingError(f"الملف '{filename}' محمي بكلمة مرور ولا يمكن قراءته.")

        try:
            pages, ocr_pages = self._read_pages(document, filename)
            page_tables = self._read_tables(document)
            metadata = self._metadata(document, len(pages), ocr_pages)
            sections = self._build_sections(document, pages, page_tables)
        finally:
            document.close()

        if not sections:
            raise ExtractionError(f"تعذر استخراج أي محتوى من الملف '{filename}'.")

        raw_text = "\n\n".join(s.content for s in sections)
        warning = ""
        if ocr_pages:
            warning = (
                f"{ocr_pages} من {len(pages)} صفحة قُرئت بالتعرّف الضوئي (OCR)؛ "
                f"قد تحتوي أخطاء قراءة."
            )

        return ParsedDocument(
            title=str(metadata.get("title") or "") or filename.rsplit(".", 1)[0],
            sections=sections,
            raw_text=raw_text,
            language=detect_language(raw_text),
            file_type=FileType.PDF,
            metadata=metadata,
            extraction_warning=warning,
        )

    # -- text, and what to do when there is none --------------------------
    def _read_pages(self, document, filename: str) -> tuple[list[str], int]:
        """Text per page, falling back to OCR only where the page has none.

        Mixed documents are the reason this is decided per page rather than per file: a
        contract is often typed with a scanned signature page or annex bound into it, and
        re-reading the typed pages through OCR would replace good text with worse.
        """
        extracted = [arabic_pdf.page_text(page) for page in document]
        empty = [i for i, text in enumerate(extracted) if len(text) < MIN_PAGE_CHARS]

        if not empty:
            return extracted, 0

        mostly_images = len(empty) / max(len(extracted), 1) >= SCANNED_SHARE
        ability = ocr.capability()

        if not self.enable_ocr or not ability.available:
            if mostly_images:
                raise OcrRequiredError(
                    f"الملف '{filename}' ممسوح ضوئيًا ({len(empty)} من {len(extracted)} "
                    f"صفحة بلا نص). {ability.reason or 'التعرّف الضوئي غير مفعّل.'}"
                )
            # A few image pages inside a readable document: keep the text there is.
            logger.info("%s: %s image page(s) skipped, OCR unavailable", filename, len(empty))
            return extracted, 0

        read = 0
        for index in empty:
            text = ocr.read_page(document[index])
            if text:
                extracted[index] = text
                read += 1

        if read == 0 and mostly_images:
            raise OcrRequiredError(
                f"الملف '{filename}' ممسوح ضوئيًا ولم ينتج التعرّف الضوئي نصًا "
                f"قابلًا للاستخدام من أي صفحة."
            )
        logger.info("%s: %s page(s) read by OCR", filename, read)
        return extracted, read

    @staticmethod
    def _read_tables(document) -> dict[int, list[list[list[str]]]]:
        """Tables per page: the ruled ones first, then the ones drawn with whitespace.

        Ruling lines are the stronger signal, so where the library finds a table its
        answer is taken. Where it finds none, the page is read again by geometry, which
        is the only thing left when a table's columns are made of alignment.

        That second pass is not a nicety. A statement laid out without rules used to
        arrive as a stream of text in which a note reference and an amount look the
        same, and an answer built on it reported the note number as a figure. The pass
        only reports what the page contains: no cell is computed, summed or filled in.
        """
        found: dict[int, list[list[list[str]]]] = {}
        by_layout = 0
        for number, page in enumerate(document, start=1):
            grids = []
            try:
                detected = page.find_tables()
            except Exception:  # noqa: BLE001
                detected = None
            for table in getattr(detected, "tables", []):
                try:
                    grid = tables.normalise_grid(table.extract())
                except Exception:  # noqa: BLE001
                    continue
                if len(grid) >= 2 and len(grid[0]) >= 2:
                    grids.append(grid)

            if not grids:
                try:
                    for recovered in layout_tables.extract(page):
                        grid = tables.normalise_grid(recovered.rows)
                        if len(grid) >= 2 and len(grid[0]) >= 2:
                            grids.append(grid)
                            by_layout += 1
                except Exception:  # noqa: BLE001 - a page costs only itself
                    logger.exception("Layout table recovery failed on page %s", number)

            if grids:
                found[number] = grids

        if by_layout:
            logger.info("Recovered %s borderless table(s) from layout", by_layout)
        return found

    # -- structure ---------------------------------------------------------
    def _build_sections(self, document, pages: list[str], page_tables: dict) -> list[Section]:
        """Sections from the headings found, or one section per page when there are none.

        Falling back to pages is deliberate. A page is a real boundary a reader can open
        to, so a document with no detectable headings still produces citations that point
        somewhere, instead of one section spanning two hundred pages.
        """
        covered = self._cells_by_page(page_tables)
        headings = self._find_headings(document, pages)
        if headings:
            return self._sections_from_headings(pages, headings, page_tables, covered)
        return self._sections_from_pages(pages, page_tables, covered)

    @staticmethod
    def _cells_by_page(page_tables: dict) -> dict[int, set[str]]:
        """Every value a page's extracted tables already account for.

        Table text is in the page text too — extraction does not remove it — so a page
        with a table otherwise carries every figure twice: once as loose lines and once
        inside the rendered grid. The duplicate costs context budget and puts the same
        number in front of the model in two different shapes, one of which has lost its
        column. The loose copy is the one dropped.
        """
        covered: dict[int, set[str]] = {}
        for number, grids in page_tables.items():
            entries: set[str] = set()
            for grid in grids:
                for row in grid:
                    cells = [cell.strip() for cell in row if cell.strip()]
                    entries.update(cells)
                    # A borderless row also has to be matched whole. Its page-text line
                    # is the row's cells run together — "TOTAL ASSETS 180,166 150,000" —
                    # which equals no single cell, so comparing cell by cell left every
                    # such line in place and the figures appeared twice: once with their
                    # columns and once without.
                    if len(cells) > 1:
                        entries.add(" ".join(cells))
            covered[number] = entries
        return covered

    @staticmethod
    def _is_covered(line: str, cells: set[str]) -> bool:
        """Whether a page-text line is already accounted for by an extracted table."""
        stripped = line.strip()
        if not stripped:
            return False
        if stripped in cells:
            return True
        # Whitespace differs between the two readings of the same row, so the
        # comparison is made on the words rather than the spacing between them.
        return " ".join(stripped.split()) in {" ".join(c.split()) for c in cells}

    def _find_headings(self, document, pages: list[str]) -> dict[int, list[tuple[str, int]]]:
        """Lines set larger than the body text, per page, with a level for each size.

        The body size is the most common size in the document, which is what a body size
        is. Everything meaningfully larger is a candidate, and the distinct larger sizes
        become levels in descending order — the largest is level 1.
        """
        sizes: Counter[float] = Counter()
        per_page: list[list[dict]] = []
        for index, page in enumerate(document):
            if index < len(pages) and not pages[index]:
                per_page.append([])
                continue
            lines = arabic_pdf.page_lines(page)
            per_page.append(lines)
            for line in lines:
                if line["text"]:
                    sizes[line["size"]] += len(line["text"])

        if not sizes:
            return {}

        body_size = sizes.most_common(1)[0][0]
        candidates = [
            size for size in sizes
            if size >= body_size * HEADING_SIZE_RATIO
        ]
        if not candidates:
            return {}

        # Largest first, so the biggest text becomes the shallowest level.
        ranked = sorted(candidates, reverse=True)
        level_of = {size: min(rank + 1, 6) for rank, size in enumerate(ranked)}

        found: dict[int, list[tuple[str, int]]] = {}
        total = 0
        for index, lines in enumerate(per_page):
            page_headings = [
                (line["text"], level_of[line["size"]])
                for line in lines
                if line["size"] in level_of and self._looks_like_heading(line["text"])
            ]
            if page_headings:
                found[index + 1] = page_headings
                total += len(page_headings)

        if total > max(len(pages), 1) * MAX_HEADINGS_PER_PAGE:
            logger.info("PDF heading detection abandoned: %s headings over %s pages", total, len(pages))
            return {}
        logger.info("PDF headings found: %s over %s page(s)", total, len(found))
        return found

    @staticmethod
    def _looks_like_heading(text: str) -> bool:
        stripped = text.strip()
        return bool(stripped) and len(stripped) <= MAX_HEADING_CHARS and not stripped.endswith(SENTENCE_END)

    def _sections_from_headings(
        self, pages: list[str], headings: dict, page_tables: dict, covered: dict
    ) -> list[Section]:
        sections: list[Section] = []
        stack: list[tuple[int, str]] = []
        current_heading = "مقدمة"
        current_level = 0
        current_page = 1
        buffer: list[str] = []
        blocks: list[Block] = []

        def flush() -> None:
            nonlocal buffer, blocks
            content = "\n".join(buffer).strip()
            if content:
                sections.append(
                    Section(
                        heading=current_heading,
                        level=current_level,
                        content=content,
                        path=[t for _, t in stack] or [current_heading],
                        blocks=list(blocks),
                        location=SourceLocation(page=current_page),
                    )
                )
            buffer, blocks = [], []

        for number, text in enumerate(pages, start=1):
            page_headings = {h for h, _ in headings.get(number, [])}
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and stripped in page_headings:
                    flush()
                    level = next(l for h, l in headings[number] if h == stripped)
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, stripped))
                    current_heading, current_level, current_page = stripped, level, number
                    continue
                if stripped and not self._is_covered(stripped, covered.get(number, set())):
                    buffer.append(stripped)
                    blocks.append(
                        Block(
                            type=BlockType.PARAGRAPH,
                            text=stripped,
                            location=SourceLocation(page=number),
                            order=len(blocks),
                        )
                    )

            for order, grid in enumerate(page_tables.get(number, []), start=1):
                rendered = self._render(grid)
                if rendered:
                    buffer.append(rendered)
                    blocks.append(
                        Block(
                            type=BlockType.TABLE,
                            text=rendered,
                            structured_data=grid,
                            location=SourceLocation(page=number, table_index=order),
                            order=len(blocks),
                        )
                    )

        flush()
        return sections

    def _sections_from_pages(
        self, pages: list[str], page_tables: dict, covered: dict
    ) -> list[Section]:
        sections: list[Section] = []
        for number, text in enumerate(pages, start=1):
            cells = covered.get(number, set())
            body = "\n".join(
                line for line in text.splitlines()
                if line.strip() and line.strip() not in cells
            ).strip()
            parts = [body] if body else []
            blocks: list[Block] = []
            if body:
                blocks.append(
                    Block(
                        type=BlockType.PARAGRAPH,
                        text=body,
                        location=SourceLocation(page=number),
                        order=0,
                    )
                )
            for order, grid in enumerate(page_tables.get(number, []), start=1):
                rendered = self._render(grid)
                if rendered:
                    parts.append(rendered)
                    blocks.append(
                        Block(
                            type=BlockType.TABLE,
                            text=rendered,
                            structured_data=grid,
                            location=SourceLocation(page=number, table_index=order),
                            order=len(blocks),
                        )
                    )
            content = "\n\n".join(parts).strip()
            if content:
                heading = f"صفحة {number}"
                sections.append(
                    Section(
                        heading=heading,
                        level=1,
                        content=content,
                        path=[heading],
                        blocks=blocks,
                        location=SourceLocation(page=number),
                    )
                )
        return sections

    @staticmethod
    def _render(grid: list[list[str]]) -> str:
        if not grid:
            return ""
        if tables.looks_like_header(grid[0], grid[1:]):
            return tables.render(grid[1:], header=grid[0])
        return tables.render(grid)

    @staticmethod
    def _metadata(document, page_count: int, ocr_pages: int) -> dict[str, object]:
        collected: dict[str, object] = {"pages": page_count}
        if ocr_pages:
            collected["ocr_pages"] = ocr_pages
        try:
            raw = document.metadata or {}
        except Exception:  # noqa: BLE001
            return collected

        for field, key in (("title", "title"), ("author", "author"), ("subject", "subject")):
            value = str(raw.get(field) or "").strip()
            if value:
                collected[key] = value

        created = str(raw.get("creationDate") or "")
        # PDF dates look like D:20240623120000+04'00'
        if created.startswith("D:") and len(created) >= 10:
            collected["date"] = f"{created[2:6]}-{created[6:8]}-{created[8:10]}"
        return collected
