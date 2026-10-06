"""One-time repairs the server runs on itself when it starts, each exactly once.

A repair that changes how documents are read has to reach the documents already read
the old way. It runs here, in the server, because re-reading a document uses the same
ingestion workers an upload does; a marker file records that it ran, so a restart does
not repeat it.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

MARKERS = BASE_DIR / "data" / "repairs"


def _done(name: str) -> bool:
    return (MARKERS / f"{name}.done").exists()


def _mark(name: str, note: str) -> None:
    MARKERS.mkdir(parents=True, exist_ok=True)
    (MARKERS / f"{name}.done").write_text(note, encoding="utf-8")


def pictured_pdfs(documents) -> list:
    """The PDFs with a typed page that is mostly picture — the pages the reader used to
    skip, so a price table pasted as an image never reached the index."""
    import pymupdf

    from app.parsers import arabic_pdf, ocr
    from app.parsers.pdf_parser import MIN_NEW_FIGURE_LINES, MIN_PAGE_CHARS, PICTURE_SHARE, PdfParser

    if not ocr.capability().available:
        return []
    found = []
    for document in documents:
        path = Path(document.stored_path or "")
        if path.suffix.lower() != ".pdf" or not path.exists():
            continue
        try:
            with pymupdf.open(str(path)) as pdf:
                # Only a file the new reading actually changes is read again — the same
                # test the parser applies, so nothing is re-indexed to come out the same.
                for page in pdf:
                    typed = arabic_pdf.page_text(page)
                    if len(typed) < MIN_PAGE_CHARS or PdfParser._picture_share(page) < PICTURE_SHARE:
                        continue
                    read = PdfParser._picture_text(pdf, page)
                    if len(PdfParser._new_figure_lines(typed, read)) >= MIN_NEW_FIGURE_LINES:
                        found.append(document)
                        break
        except Exception:  # noqa: BLE001 - one unreadable file does not stop the rest
            logger.exception("Could not inspect %s", path.name)
    return found


def reread_pictured_pdfs(container) -> None:
    name = "2026-10-05-pictured-pdf-pages"
    if _done(name):
        return
    from sqlalchemy import select

    from app.models.database import session_scope
    from app.models.document import Document

    with session_scope() as session:
        documents = list(session.scalars(select(Document).where(Document.status == "completed")))
        targets = pictured_pdfs(documents)
        names = [d.filename for d in targets]
        for document in targets:
            container.document_service.reindex(session, document.id)
    logger.info("Re-reading %s PDF(s) with pictures on typed pages: %s", len(names), names)
    _mark(name, "\n".join(names) or "none")


def damaged_pdfs(documents) -> list:
    """The PDFs with a page whose text layer does not match what it shows."""
    import pymupdf

    from app.parsers import arabic_pdf
    from app.parsers.text_quality import DAMAGED, damage

    found = []
    for document in documents:
        path = Path(document.stored_path or "")
        if path.suffix.lower() != ".pdf" or not path.exists():
            continue
        try:
            with pymupdf.open(str(path)) as pdf:
                if any(damage(arabic_pdf.page_text(page)) >= DAMAGED for page in pdf):
                    found.append(document)
        except Exception:  # noqa: BLE001
            logger.exception("Could not inspect %s", path.name)
    return found


def reread_damaged_pdfs(container) -> None:
    name = "2026-10-06-damaged-text-layers"
    if _done(name):
        return
    from sqlalchemy import select

    from app.models.database import session_scope
    from app.models.document import Document

    with session_scope() as session:
        documents = list(session.scalars(select(Document).where(Document.status == "completed")))
        targets = damaged_pdfs(documents)
        names = [d.filename for d in targets]
        for document in targets:
            container.document_service.reindex(session, document.id)
    logger.info("Re-reading %s PDF(s) with damaged text layers: %s", len(names), names)
    _mark(name, "\n".join(names) or "none")


def run_pending(container) -> None:
    """In the background: startup is not held for it, and a failure is logged and tried
    again at the next start rather than stopping the server."""
    def work() -> None:
        try:
            reread_damaged_pdfs(container)
        except Exception:  # noqa: BLE001
            logger.exception("One-time repair failed; it will be tried at the next start")
        try:
            reread_pictured_pdfs(container)
        except Exception:  # noqa: BLE001
            logger.exception("One-time repair failed; it will be tried at the next start")

    threading.Thread(target=work, name="one-time-repairs", daemon=True).start()
