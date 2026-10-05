"""Pictures: photographed papers, screenshots, scans saved as images.

Read with the same OCR engine, languages and repairs as a scanned PDF page, so a letter
photographed on a phone and the same letter scanned to PDF become the same text. A
multi-page TIFF is read page by page and cited by page. A picture with no readable text —
a site photo, a logo — is refused with that reason rather than indexed as nothing.
"""

from __future__ import annotations

import io
from pathlib import Path

from app.core.domain import FileType, ParsedDocument, Section, SourceLocation
from app.core.text import detect_language
from app.exceptions import OcrRequiredError, ParsingError
from app.parsers import ocr
from app.parsers.base import DocumentParser

#: Pages read at most from one multi-page image; beyond this it is a document, and
#: belongs in a PDF.
MAX_FRAMES = 50


class ImageParser(DocumentParser):
    name = "image"
    supported_extensions = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        ability = ocr.capability()
        if not ability.available:
            raise OcrRequiredError(f"الصورة '{filename}' تحتاج قراءة ضوئية غير متاحة على الخادم: {ability.reason}")

        try:
            from PIL import Image, ImageSequence

            picture = Image.open(io.BytesIO(content))
            frames = [frame.copy() for frame in ImageSequence.Iterator(picture)][:MAX_FRAMES]
        except Exception as exc:  # noqa: BLE001 - Pillow raises many kinds for a bad file
            raise ParsingError(f"تعذر فتح الصورة '{filename}': الملف تالف أو ليس صورة.") from exc

        title = Path(filename).stem
        sections: list[Section] = []
        for number, frame in enumerate(frames, start=1):
            text = ocr.read_image(frame)
            if not text:
                continue
            heading = f"{title} — صفحة {number}" if len(frames) > 1 else title
            sections.append(Section(
                heading=heading, level=1, content=text, path=[heading],
                location=SourceLocation(page=number),
            ))

        if not sections:
            raise ParsingError(
                f"لم يُعثر على نص مقروء في الصورة '{filename}'. "
                "إن كانت صورة لمستند، صوّره بإضاءة جيدة ومن مسافة أقرب."
            )
        raw_text = "\n\n".join(s.content for s in sections)
        return ParsedDocument(
            title=title,
            sections=sections,
            raw_text=raw_text,
            language=detect_language(raw_text),
            metadata={"pages": len(frames), "ocr_pages": len(sections)},
            file_type=FileType.IMAGE,
        )
