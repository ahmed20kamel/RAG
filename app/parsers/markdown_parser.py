"""Markdown parser: front matter, heading tree, and code-fence-aware scanning."""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from app.core.domain import ParsedDocument, Section
from app.core.text import detect_language
from app.exceptions import ParsingError
from app.parsers.base import DocumentParser

logger = logging.getLogger(__name__)

ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
SETEXT_UNDERLINE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
FRONT_MATTER_DELIM = re.compile(r"^---\s*$")

FRONT_MATTER_KEYS = {
    "title",
    "category",
    "source",
    "version",
    "date",
    "language",
    "author",
    "tags",
    "project",
    "reference",
}


class MarkdownParser(DocumentParser):
    name = "markdown"
    supported_extensions = (".md", ".markdown")

    def __init__(self, max_heading_depth: int = 4) -> None:
        self.max_heading_depth = max_heading_depth

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        text = self._decode(content)
        if not text.strip():
            raise ParsingError(f"الملف '{filename}' فارغ أو لا يحتوي على نص قابل للقراءة.")

        metadata, body = self._extract_front_matter(text)
        lines = body.splitlines()
        sections = self._build_sections(lines)

        if not sections:
            raise ParsingError(f"تعذر استخراج أي محتوى من الملف '{filename}'.")

        title = self._resolve_title(metadata, sections, filename)
        language = detect_language(body)

        return ParsedDocument(
            title=title,
            sections=sections,
            raw_text=body,
            language=language,
            metadata=metadata,
        )

    @staticmethod
    def _decode(content: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
            try:
                return content.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeDecodeError:
                continue
        raise ParsingError("تعذر فك ترميز الملف. الرجاء حفظه بترميز UTF-8.")

    @staticmethod
    def _extract_front_matter(text: str) -> tuple[dict[str, Any], str]:
        lines = text.splitlines()
        if not lines or not FRONT_MATTER_DELIM.match(lines[0]):
            return {}, text

        closing = next((i for i in range(1, len(lines)) if FRONT_MATTER_DELIM.match(lines[i])), None)
        if closing is None:
            return {}, text

        raw_block = "\n".join(lines[1:closing])
        body = "\n".join(lines[closing + 1 :])
        try:
            loaded = yaml.safe_load(raw_block) or {}
        except yaml.YAMLError as exc:
            logger.warning("Front matter is not valid YAML, ignoring it: %s", exc)
            return {}, body

        if not isinstance(loaded, dict):
            return {}, body

        metadata = {
            str(key).strip().lower(): value
            for key, value in loaded.items()
            if str(key).strip().lower() in FRONT_MATTER_KEYS and value not in (None, "")
        }
        return metadata, body

    def _build_sections(self, lines: list[str]) -> list[Section]:
        sections: list[Section] = []
        heading_stack: list[tuple[int, str]] = []
        current_heading = "مقدمة"
        current_level = 0
        current_path: list[str] = []
        buffer: list[str] = []
        order = 0

        fence_marker: str | None = None
        index = 0

        def flush() -> None:
            nonlocal order, buffer
            content = "\n".join(buffer).strip()
            if not content and not sections and not current_path:
                buffer = []
                return
            if content or heading_stack:
                sections.append(
                    Section(
                        heading=current_heading,
                        level=current_level,
                        content=content,
                        path=list(current_path) or [current_heading],
                        order=order,
                    )
                )
                order += 1
            buffer = []

        while index < len(lines):
            line = lines[index]

            fence_match = FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence_marker is None:
                    fence_marker = marker[0] * 3
                elif marker.startswith(fence_marker):
                    fence_marker = None
                buffer.append(line)
                index += 1
                continue

            if fence_marker is not None:
                buffer.append(line)
                index += 1
                continue

            heading_text, level = self._match_heading(lines, index)
            if heading_text is not None:
                flush()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, heading_text))
                current_heading = heading_text
                current_level = level
                current_path = [text for _, text in heading_stack]
                index += 2 if self._is_setext(lines, index) else 1
                continue

            buffer.append(line)
            index += 1

        flush()
        return [s for s in sections if s.content.strip() or s.level > 0]

    def _match_heading(self, lines: list[str], index: int) -> tuple[str | None, int]:
        line = lines[index]
        atx = ATX_HEADING.match(line)
        if atx:
            text = atx.group(2).strip()
            return (text or "بدون عنوان", len(atx.group(1)))

        if self._is_setext(lines, index):
            underline = lines[index + 1].strip()
            return lines[index].strip(), 1 if underline.startswith("=") else 2

        return None, 0

    @staticmethod
    def _is_setext(lines: list[str], index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        current = lines[index].strip()
        if not current or current.startswith(("-", "*", "+", ">", "|", "#")):
            return False
        underline = lines[index + 1]
        return bool(SETEXT_UNDERLINE.match(underline)) and len(underline.strip()) >= 2

    @staticmethod
    def _resolve_title(metadata: dict[str, Any], sections: list[Section], filename: str) -> str:
        if metadata.get("title"):
            return str(metadata["title"]).strip()
        top = next((s for s in sections if s.level == 1), None)
        if top:
            return top.heading
        first_headed = next((s for s in sections if s.level > 0), None)
        if first_headed:
            return first_headed.heading
        return filename.rsplit(".", 1)[0]
