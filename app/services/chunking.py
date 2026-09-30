"""Heading-aware chunking.

Sections are the primary boundary. Inside a section, content is split into atomic
blocks (code fences, tables, list groups, paragraphs) which are packed into chunks
so that a table, list or code block is never cut in the middle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.domain import Chunk, ParsedDocument, Section

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
TABLE_ROW = re.compile(r"^\s{0,3}\|")
LIST_ITEM = re.compile(r"^\s{0,3}([-*+]|\d{1,3}[.)])\s+")
SENTENCE_END = re.compile(r"(?<=[.!?؟。])\s+|\n")


@dataclass(slots=True)
class _Unit:
    """Text belonging to one section, before it becomes a Chunk."""

    section: Section
    text: str
    #: Normally the section's own locator. Cleared when small units from different
    #: places are folded together, because the result is no longer on one page.
    locator: str | None = None

    @property
    def where(self) -> str:
        return self.section.locator if self.locator is None else self.locator


class HeadingAwareChunker:
    def __init__(self, chunk_size: int, chunk_overlap: int, min_chunk_size: int) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def chunk(self, parsed: ParsedDocument, document_id: str, filename: str) -> list[Chunk]:
        units: list[_Unit] = []
        for section in parsed.sections:
            content = section.content.strip()
            if not content:
                continue
            for piece in self._pack(self._split_blocks(content)):
                units.append(_Unit(section=section, text=piece))

        units = self._merge_small_units(units)
        by_id = {s.section_id: s for s in parsed.sections if s.section_id}

        chunks: list[Chunk] = []
        for index, unit in enumerate(units):
            section = unit.section
            breadcrumb = section.breadcrumb
            parent = by_id.get(section.parent_id or "")
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}::{index}",
                    document_id=document_id,
                    filename=filename,
                    index=index,
                    heading=section.heading,
                    section=breadcrumb,
                    content=unit.text,
                    embed_text=self._build_embed_text(parsed.title, breadcrumb, unit.text),
                    heading_level=section.level,
                    char_count=len(unit.text),
                    section_id=section.section_id,
                    parent_section_id=section.parent_id,
                    parent_section=parent.breadcrumb if parent else "",
                    document_title=parsed.title,
                    has_table=self._any_line(unit.text, TABLE_ROW),
                    has_list=self._any_line(unit.text, LIST_ITEM),
                    has_code=self._any_line(unit.text, FENCE),
                    # Copied from the section, never derived here. Markdown sections
                    # carry no location, so this stays empty and every existing chunk
                    # is built exactly as before.
                    locator=unit.where,
                    page=(
                        section.location.page
                        if unit.where and section.location
                        else None
                    ),
                )
            )
        return chunks

    @staticmethod
    def _any_line(text: str, pattern: re.Pattern[str]) -> bool:
        """These patterns are anchored per line, so they must be applied line by line."""
        return any(pattern.match(line) for line in text.split("\n"))

    @staticmethod
    def _build_embed_text(title: str, breadcrumb: str, content: str) -> str:
        """Prefix the heading path so an isolated chunk keeps its context in vector space."""
        return f"{title}\n{breadcrumb}\n\n{content}".strip()

    def _split_blocks(self, content: str) -> list[str]:
        lines = content.split("\n")
        blocks: list[str] = []
        buffer: list[str] = []
        index = 0

        def flush() -> None:
            nonlocal buffer
            text = "\n".join(buffer).strip()
            if text:
                blocks.append(text)
            buffer = []

        while index < len(lines):
            line = lines[index]

            fence_match = FENCE.match(line)
            if fence_match:
                flush()
                marker = fence_match.group(1)[0] * 3
                fenced = [line]
                index += 1
                while index < len(lines):
                    fenced.append(lines[index])
                    closing = FENCE.match(lines[index])
                    index += 1
                    if closing and closing.group(1).startswith(marker):
                        break
                blocks.append("\n".join(fenced))
                continue

            if TABLE_ROW.match(line):
                flush()
                table = []
                while index < len(lines) and TABLE_ROW.match(lines[index]):
                    table.append(lines[index])
                    index += 1
                blocks.append("\n".join(table))
                continue

            if LIST_ITEM.match(line):
                flush()
                items: list[str] = []
                while index < len(lines):
                    candidate = lines[index]
                    is_item = bool(LIST_ITEM.match(candidate))
                    is_continuation = candidate.startswith((" ", "\t")) and candidate.strip()
                    if not (is_item or is_continuation):
                        break
                    items.append(candidate)
                    index += 1
                blocks.append("\n".join(items))
                continue

            if not line.strip():
                flush()
                index += 1
                continue

            buffer.append(line)
            index += 1

        flush()
        return blocks

    def _pack(self, blocks: list[str]) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for block in blocks:
            if len(block) > self.chunk_size:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_len = [], 0
                chunks.extend(self._split_oversized(block))
                continue

            projected = current_len + len(block) + (2 if current else 0)
            if current and projected > self.chunk_size:
                chunks.append("\n\n".join(current))
                current = self._overlap_blocks(current)
                current_len = sum(len(b) + 2 for b in current)

            current.append(block)
            current_len += len(block) + 2

        if current:
            chunks.append("\n\n".join(current))
        return [c.strip() for c in chunks if c.strip()]

    def _overlap_blocks(self, blocks: list[str]) -> list[str]:
        """Carry the tail of the previous chunk forward so context survives the cut."""
        if self.chunk_overlap <= 0:
            return []
        carried: list[str] = []
        total = 0
        for block in reversed(blocks):
            if total + len(block) > self.chunk_overlap:
                break
            carried.insert(0, block)
            total += len(block)
        return carried

    def _split_oversized(self, block: str) -> list[str]:
        if FENCE.match(block):
            return self._split_code_block(block)
        if TABLE_ROW.match(block):
            return self._split_table(block)
        return self._split_prose(block)

    def _split_code_block(self, block: str) -> list[str]:
        lines = block.split("\n")
        opening = lines[0]
        closing_marker = opening.strip()[:3]
        body = lines[1:-1] if len(lines) > 2 and FENCE.match(lines[-1]) else lines[1:]

        parts: list[str] = []
        current: list[str] = []
        current_len = 0
        budget = self.chunk_size - len(opening) - len(closing_marker) - 4

        for line in body:
            if current and current_len + len(line) + 1 > budget:
                parts.append("\n".join([opening, *current, closing_marker]))
                current, current_len = [], 0
            current.append(line)
            current_len += len(line) + 1

        if current:
            parts.append("\n".join([opening, *current, closing_marker]))
        return parts

    def _split_table(self, block: str) -> list[str]:
        rows = block.split("\n")
        header = rows[:2] if len(rows) > 2 else rows[:1]
        header_text = "\n".join(header)
        body = rows[len(header) :]

        parts: list[str] = []
        current: list[str] = []
        current_len = len(header_text)

        for row in body:
            if current and current_len + len(row) + 1 > self.chunk_size:
                parts.append("\n".join([header_text, *current]))
                current, current_len = [], len(header_text)
            current.append(row)
            current_len += len(row) + 1

        if current:
            parts.append("\n".join([header_text, *current]))
        return parts or [block]

    def _split_prose(self, block: str) -> list[str]:
        pieces = [p.strip() for p in SENTENCE_END.split(block) if p and p.strip()]
        parts: list[str] = []
        current: list[str] = []
        current_len = 0

        for piece in pieces:
            if len(piece) > self.chunk_size:
                if current:
                    parts.append(" ".join(current))
                    current, current_len = [], 0
                parts.extend(
                    piece[i : i + self.chunk_size] for i in range(0, len(piece), self.chunk_size)
                )
                continue
            if current and current_len + len(piece) + 1 > self.chunk_size:
                parts.append(" ".join(current))
                current, current_len = [], 0
            current.append(piece)
            current_len += len(piece) + 1

        if current:
            parts.append(" ".join(current))
        return parts

    def _merge_small_units(self, units: list[_Unit]) -> list[_Unit]:
        """Fold undersized units into the next one when they share a heading ancestor."""
        if not units:
            return units

        merged: list[_Unit] = []
        pending: _Unit | None = None

        for unit in units:
            if pending is None:
                pending = unit
                continue

            combined_len = len(pending.text) + len(unit.text) + 2
            if (
                len(pending.text) < self.min_chunk_size
                and combined_len <= self.chunk_size
                and self._are_siblings(pending.section, unit.section)
                # Never across two places in the file. Short sections on facing pages
                # would otherwise fold into one chunk that is on neither, and the choice
                # is then between citing the wrong page — confidently, since a page
                # number looks checkable — or citing none, which throws away the thing a
                # PDF citation is for. Keeping them apart costs a slightly smaller chunk.
                # Markdown units all carry an empty locator, so they always compare equal
                # and nothing about the existing corpus changes.
                and pending.where == unit.where
            ):
                header = f"{'#' * max(unit.section.level, 1)} {unit.section.heading}"
                anchor = unit.section if len(unit.text) > len(pending.text) else pending.section
                pending = _Unit(
                    section=anchor,
                    text=f"{pending.text}\n\n{header}\n{unit.text}",
                    locator=pending.where,
                )
                continue

            merged.append(pending)
            pending = unit

        if pending is not None:
            merged.append(pending)
        return merged

    @staticmethod
    def _are_siblings(first: Section, second: Section) -> bool:
        """Only merge sections under the same parent, so the cited breadcrumb stays accurate."""
        return bool(first.path) and bool(second.path) and first.path[:-1] == second.path[:-1]
