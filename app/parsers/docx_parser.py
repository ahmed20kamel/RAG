"""Word documents, read for the structure that makes them answerable.

The heading tree is the whole point. Everything downstream — the breadcrumb on a chunk,
the section a citation names, the expansion that pulls in neighbouring text — is built on
sections, and a Word file that arrives as one flat block of prose retrieves badly and
cites uselessly.

Which is why heading detection here is tiered rather than trusting `Heading 1`. The
company's own documents were read first, and not one of them uses a heading style: every
paragraph is `Normal`, and the headings are bold, short and numbered — "1. الهدف من
الشغل". A parser that only understood styles would have turned a 306-paragraph report
into a single section and called it a success.

So three tiers, most trustworthy first:

1. the style says so — `Heading 2`, `Title`, or the Arabic `عنوان 2`;
2. Word's own `outlineLvl`, which survives when somebody renamed the style;
3. and only when neither found anything, the shape of the text itself.

The third tier is deliberately the last resort and deliberately conservative, because
over-detecting headings shreds a document into fragments too small to answer from.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO

from app.core.domain import (
    Block,
    BlockType,
    FileType,
    ParsedDocument,
    Section,
    SourceLocation,
)
from app.core.text import detect_language
from app.exceptions import ExtractionError, ParsingError
from app.parsers import tables
from app.parsers.base import DocumentParser
from app.parsers.text_repair import repair_arabic

logger = logging.getLogger(__name__)

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

#: Heading styles in the languages Word ships them in. The number is the level.
HEADING_STYLE = re.compile(
    r"^(?:heading|título|titre|überschrift|заголовок|عنوان)\s*(\d)$", re.IGNORECASE
)
TITLE_STYLES = {"title", "subtitle", "العنوان", "عنوان رئيسي"}

#: "1." / "1.1" / "1.1.1" / "أولاً" / "A." — the depth of the numbering is the level.
NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)\-–]?\s+\S")
ARABIC_ORDINAL = re.compile(
    r"^\s*(?:أولا|أولاً|ثانيا|ثانياً|ثالثا|ثالثاً|رابعا|رابعاً|خامسا|خامساً|"
    r"سادسا|سادساً|سابعا|سابعاً|ثامنا|ثامناً|تاسعا|تاسعاً|عاشرا|عاشراً)\s*[:.\-–]?\s*"
)

#: A heading is a label, not a sentence. These end sentences, so they end the guess too.
SENTENCE_END = ("۔", ".", "!", "؟", "?", ":", "،", ",", ";", "؛")

MAX_HEADING_CHARS = 120
MIN_STRUCTURAL_HEADINGS = 3

#: Above this share of the document, "bold and short" is a writing style rather than a
#: structure, and treating every one as a heading would fragment the text.
MAX_HEURISTIC_SHARE = 0.35


class DocxParser(DocumentParser):
    name = "docx"
    supported_extensions = (".docx",)

    def __init__(self, max_heading_depth: int = 6) -> None:
        self.max_heading_depth = max_heading_depth

    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        try:
            import docx
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ParsingError("مكتبة قراءة Word غير مثبتة على الخادم.") from exc

        try:
            document = docx.Document(BytesIO(content))
        except Exception as exc:  # noqa: BLE001 - python-docx raises broadly
            raise ParsingError(
                f"تعذر فتح المستند '{filename}'. قد يكون تالفًا أو محميًا بكلمة مرور."
            ) from exc

        items = list(self._walk(document))
        if not items:
            raise ExtractionError(f"المستند '{filename}' لا يحتوي على نص أو جداول.")

        headings = self._resolve_headings(items)
        sections = self._build_sections(items, headings)
        if not sections:
            raise ExtractionError(f"تعذر استخراج أي محتوى من المستند '{filename}'.")

        metadata = self._core_properties(document)
        raw_text = "\n\n".join(s.content for s in sections)

        return ParsedDocument(
            title=str(metadata.get("title") or "") or self._title_from(sections, filename),
            sections=sections,
            raw_text=raw_text,
            language=detect_language(raw_text),
            file_type=FileType.DOCX,
            metadata=metadata,
        )

    # -- reading the body in order ----------------------------------------
    def _walk(self, document):
        """Paragraphs and tables in the order they appear on the page.

        python-docx exposes `.paragraphs` and `.tables` as two separate lists, and
        reading one then the other puts every table at the end of the document — under
        whichever heading happened to come last. The body XML is walked instead, so a
        table stays under the heading it was written beneath.
        """
        import docx.table
        import docx.text.paragraph

        body = document.element.body
        paragraph_index = 0
        table_index = 0

        for child in body.iterchildren():
            if child.tag == f"{W_NS}p":
                paragraph = docx.text.paragraph.Paragraph(child, document)
                text = repair_arabic(paragraph.text).strip()
                paragraph_index += 1
                if text:
                    yield ("p", paragraph, text, paragraph_index)
            elif child.tag == f"{W_NS}tbl":
                table = docx.table.Table(child, document)
                table_index += 1
                yield ("t", table, "", table_index)

    # -- deciding what is a heading ---------------------------------------
    def _resolve_headings(self, items) -> dict[int, int]:
        """A level for each paragraph that is a heading, keyed by its position in `items`.

        The tiers are tried whole rather than per paragraph: a document either marks its
        headings structurally or it does not, and mixing a document's real `Heading 2`
        styles with guesses about its bold paragraphs produces a tree with two different
        meanings of depth in it.
        """
        structural: dict[int, int] = {}
        for index, (kind, obj, text, _) in enumerate(items):
            if kind != "p":
                continue
            level = self._level_from_style(obj) or self._level_from_outline(obj)
            if level:
                structural[index] = min(level, self.max_heading_depth)

        if len(structural) >= MIN_STRUCTURAL_HEADINGS:
            logger.info("DOCX headings from styles/outline: %s", len(structural))
            return structural

        heuristic = self._headings_from_shape(items)
        if heuristic:
            logger.info("DOCX headings from text shape: %s", len(heuristic))
            return heuristic
        return structural

    @staticmethod
    def _level_from_style(paragraph) -> int | None:
        """The level a heading style declares, if the paragraph carries one.

        `paragraph.style` is None in documents produced by some exporters, which is why
        this is guarded — a real file on this machine crashed a first pass here.
        """
        style = getattr(paragraph, "style", None)
        name = getattr(style, "name", None)
        if not name:
            return None
        matched = HEADING_STYLE.match(name.strip())
        if matched:
            return int(matched.group(1))
        if name.strip().lower() in TITLE_STYLES:
            return 1
        return None

    @staticmethod
    def _level_from_outline(paragraph) -> int | None:
        """Word's own outline level, which survives a renamed style."""
        try:
            properties = paragraph._p.find(f"{W_NS}pPr")
            if properties is None:
                return None
            outline = properties.find(f"{W_NS}outlineLvl")
            if outline is None:
                return None
            value = outline.get(f"{W_NS}val")
            # outlineLvl counts from zero; 9 means body text.
            level = int(value) + 1
            return level if 1 <= level <= 9 else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _headings_from_shape(self, items) -> dict[int, int]:
        """Headings guessed from how the text looks, used only when nothing declared any.

        A candidate is bold all the way through, short, and does not end like a sentence.
        That alone over-fires — "Team = Supervisor + Workers" in a real report is bold,
        short and unpunctuated, and it is emphasis, not a heading. So when the document
        numbers its headings, only the numbered ones are taken, and the emphasis is left
        where it belongs: inside the body text.
        """
        candidates: list[tuple[int, str]] = []
        paragraphs = 0
        for index, (kind, obj, text, _) in enumerate(items):
            if kind != "p":
                continue
            paragraphs += 1
            if self._looks_like_heading(obj, text):
                candidates.append((index, text))

        if not candidates or not paragraphs:
            return {}

        numbered = [(i, t) for i, t in candidates if NUMBERED.match(t) or ARABIC_ORDINAL.match(t)]
        if len(numbered) >= MIN_STRUCTURAL_HEADINGS:
            # Numbering is strong evidence on its own: nobody numbers emphasis. This
            # path takes no density guard, because a short document with a heading above
            # every paragraph is a legitimately dense outline, not a prose style — and a
            # ratio test cannot tell those apart while numbering can.
            return {index: self._numbering_depth(text) for index, text in numbered}

        if len(candidates) / paragraphs > MAX_HEURISTIC_SHARE:
            # Unnumbered, and bold short lines make up much of the document. That is how
            # some people write, not how they structure, and treating each one as a
            # heading would cut the text into fragments too small to answer from.
            logger.info(
                "DOCX heading guess abandoned: %s of %s paragraphs matched",
                len(candidates), paragraphs,
            )
            return {}

        return {index: self._numbering_depth(text) for index, text in candidates}

    @staticmethod
    def _looks_like_heading(paragraph, text: str) -> bool:
        if len(text) > MAX_HEADING_CHARS or text.endswith(SENTENCE_END):
            return False
        runs = [r for r in paragraph.runs if r.text.strip()]
        if not runs:
            return False
        return all(bool(run.bold) for run in runs)

    def _numbering_depth(self, text: str) -> int:
        """How deep the numbering goes: "3." is level 1, "3.2.1" is level 3."""
        matched = NUMBERED.match(text)
        if matched:
            return min(matched.group(1).count(".") + 1, self.max_heading_depth)
        return 1

    # -- assembling sections ----------------------------------------------
    def _build_sections(self, items, headings: dict[int, int]) -> list[Section]:
        sections: list[Section] = []
        stack: list[tuple[int, str]] = []

        heading_text = "مقدمة"
        level = 0
        path: list[str] = []
        body: list[str] = []
        blocks: list[Block] = []
        location: SourceLocation | None = None

        def flush() -> None:
            nonlocal body, blocks, location
            content = "\n\n".join(part for part in body if part.strip()).strip()
            if content:
                sections.append(
                    Section(
                        heading=heading_text,
                        level=level,
                        content=content,
                        path=list(path) or [heading_text],
                        blocks=list(blocks),
                        location=_section_location(blocks, location),
                    )
                )
            body, blocks, location = [], [], None

        def _section_location(
            found: list[Block], fallback: SourceLocation | None
        ) -> SourceLocation | None:
            """What a citation into this section should name beyond the section itself.

            A Word section is already identified by its heading path, which the citation
            prints, so a paragraph number adds nothing a reader can use — it renders
            empty and the citation reads as it always did.

            A table is different, and worth naming. But only when there is exactly one:
            with two tables in a section, a chunk could come from either, and "جدول 1"
            on text taken from the second is a confident citation to the wrong place.
            Saying less is the honest option.
            """
            table_blocks = [b for b in found if b.type == BlockType.TABLE and b.location]
            if len(table_blocks) == 1:
                return table_blocks[0].location
            return fallback

        for index, (kind, obj, text, ordinal) in enumerate(items):
            if kind == "p" and index in headings:
                flush()
                new_level = headings[index]
                while stack and stack[-1][0] >= new_level:
                    stack.pop()
                stack.append((new_level, text))
                heading_text = text
                level = new_level
                path = [t for _, t in stack]
                location = SourceLocation(paragraph_index=ordinal)
                continue

            if kind == "p":
                body.append(text)
                blocks.append(
                    Block(
                        type=BlockType.PARAGRAPH,
                        text=text,
                        location=SourceLocation(paragraph_index=ordinal),
                        order=len(blocks),
                    )
                )
                if location is None:
                    location = SourceLocation(paragraph_index=ordinal)
            else:
                rendered, grid = self._render_table(obj)
                if not rendered:
                    continue
                body.append(rendered)
                blocks.append(
                    Block(
                        type=BlockType.TABLE,
                        text=rendered,
                        structured_data=grid,
                        location=SourceLocation(table_index=ordinal),
                        order=len(blocks),
                    )
                )

        flush()
        return sections

    @staticmethod
    def _render_table(table) -> tuple[str, list[list[str]]]:
        """A Word table as a Markdown pipe table, plus the grid it came from.

        Merged cells repeat their text across the span in the XML, which is what a
        reader sees too, so they are left as they are rather than blanked — a blank in
        a value column would be read as missing data.
        """
        rows: list[list[str]] = []
        for row in table.rows:
            try:
                rows.append([repair_arabic(cell.text) for cell in row.cells])
            except (IndexError, AttributeError):
                # Irregular or nested tables can raise here; the rest of the table is
                # still worth keeping.
                continue

        grid = tables.normalise_grid(rows)
        if not grid:
            return "", []

        header: list[str] | None = None
        body = grid
        if tables.looks_like_header(grid[0], grid[1:]):
            header, body = grid[0], grid[1:]
        return tables.render(body, header=header), grid

    @staticmethod
    def _core_properties(document) -> dict[str, object]:
        """Whatever Word recorded about the document, where it is usable."""
        try:
            properties = document.core_properties
        except Exception:  # noqa: BLE001
            return {}

        collected: dict[str, object] = {}
        for field, key in (
            ("title", "title"),
            ("author", "author"),
            ("category", "category"),
            ("subject", "subject"),
            ("revision", "version"),
        ):
            value = getattr(properties, field, None)
            if value:
                collected[key] = str(value).strip()

        created = getattr(properties, "created", None)
        if created:
            collected["date"] = created.date().isoformat()
        return collected

    @staticmethod
    def _title_from(sections: list[Section], filename: str) -> str:
        top = next((s for s in sections if s.level == 1), None)
        if top:
            return top.heading
        return filename.rsplit(".", 1)[0]
