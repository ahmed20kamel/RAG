"""Answers delivered as files: PDF to read, Word to edit, Excel for numbers.

The model writes content; this module makes the file. A model asked to emit a file
directly produces broken spreadsheets and PDFs whose Arabic falls apart — so it is only
ever asked for what it is good at, text and Markdown tables, and everything about the
file itself is done here, the same way every time: right-to-left, Arabic shaping, a
header with the title and date, the sources listed at the end.

Every number placed in a spreadsheet cell is looked for in the passages the answer was
built from. One that is not found is highlighted and noted, so a figure the model
produced without support never sits in a clean cell looking like a quotation.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.exceptions import DocumentNotFoundError

FORMATS = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel"}
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
#: Files are kept this long, then removed by the nightly job.
KEEP_DAYS = 7

# -- what was asked for ----------------------------------------------------

_MAKE = (
    r"(?:اعمل|اعملي|إعمل|اعملى|سوّي|سوي|سويلي|هات|هاتلي|هاتلى|ابعت|ابعتلي|ارسل|أرسل|"
    r"حول|حوّل|حولي|حوّلي|صدّر|صدر|طلّع|طلع|طلعلي|جهز|جهّز|جهزلي|انشئ|أنشئ|اكتب|اكتبلي|"
    r"اعطني|أعطني|عايز|عاوز|ابغى|أبغى|ابي|أبي|نزّل|نزل|"
    r"generate|create|make|export|give\s+me|convert|prepare|send\s+me)"
)
_PDF = r"(?:pdf|بي\s*دي\s*[اإ]ف|بى\s*دى\s*[اإ]ف|بدف)"
_WORD = r"(?:word|docx|وورد|ورد)"
_EXCEL = r"(?:excel|xlsx|اكسل|إكسل|اكسيل|إكسيل|اكسال|شيت|spreadsheet)"
_FORMAT = re.compile(rf"(?<![\w.])({_PDF}|{_WORD}|{_EXCEL})(?![\w])", re.IGNORECASE)
_ASKS = re.compile(rf"(?:^|\s|و){_MAKE}", re.IGNORECASE)
_AS_FILE = re.compile(r"(?:بصيغة|على\s+شكل|في\s+ملف|كملف|ملف|as\s+an?|in|to)\s*$", re.IGNORECASE)

_CONVERSATION = re.compile(r"(?:المحادث[ةه]|الشات|المحاور[ةه]|كل\s+(?:الكلام|اللي\s+فات)|conversation|chat)", re.IGNORECASE)
_SUMMARY = re.compile(r"(?:ملخص|لخص|تلخيص|ملخّص|summary|summari[sz]e)", re.IGNORECASE)
_THAT = re.compile(
    r"^(?:دا|ده|دي|هذا|هذه|هذي|الإجابة|الاجابة|الرد|الجواب|الكلام\s+(?:دا|ده|هذا)|الجدول\s+(?:دا|ده|هذا)|"
    r"it|this|that|the\s+answer)?$",
    re.IGNORECASE,
)
#: Whole words dropped from a file request to leave the question it carries.
_FILLER_WORDS = {
    "لي", "لى", "ملف", "ملفات", "بصيغة", "كملف", "فايل", "نسخة", "فيه", "فيها", "عليه", "عليها",
    "please", "a", "an", "the", "file", "as", "in", "to", "into", "for", "me", "of", "with",
    "لـ", "ل", "ب", "بـ", "يا", "من", "فضلك", "لو", "سمحت", "شكل", "على", "في", "صيغة",
}
_FILLER_PHRASES = re.compile(r"(?:على|في)\s+شكل|من\s+فضلك|لو\s+سمحت|بصيغة", re.IGNORECASE)
_VERB = re.compile(rf"^(?:و)?{_MAKE}(?:لي|لى|ي|ني)?$", re.IGNORECASE)


def _without_request_words(text: str) -> str:
    text = _FILLER_PHRASES.sub(" ", text)
    for _ in range(3):
        text = re.sub(rf"^\s*(?:و)?{_MAKE}(?:لي|لى|ي|ني)?(?=\s|$)", "", text, flags=re.IGNORECASE)
    words = text.split()
    # Request words lead the sentence ("اعمل لي ملف …") or trail it ("… كملف"); the
    # same words in the middle belong to the question ("في أمر التغيير").
    while words and (words[0].lower() in _FILLER_WORDS or _VERB.match(words[0])):
        words.pop(0)
    while words and (words[-1].lower() in _FILLER_WORDS or _VERB.match(words[-1])):
        words.pop()
    # "بأسعار …", "بالغرامات": the attached "with" of the request, not part of the word.
    if words and len(words[0]) > 4 and (
        re.match(r"^ب(?=ال|أ|ا)", words[0])
        or words[0][1:] in {"جدول", "قائمة", "ملخص", "مقارنة", "تفاصيل", "بنود", "كل", "جميع", "ارقام", "أرقام"}
    ) and words[0].startswith("ب"):
        words[0] = words[0][1:]
    return " ".join(words)


@dataclass
class FileRequest:
    format: str
    #: "last" — the answer before this one; "conversation" — the whole conversation;
    #: "question" — a new question, answered and delivered as the file.
    subject: str
    question: str = ""


def detect(question: str) -> FileRequest | None:
    """A request for a file, or None for an ordinary question.

    Needs both a verb of making or giving and a format named as such: "ما في ملف
    العقد.pdf؟" names a PDF and asks nothing of the kind, and is answered as before.
    """
    text = " ".join(question.split())
    found = _FORMAT.search(text)
    if found is None or not _ASKS.search(text):
        return None
    word = found.group(1).lower()
    fmt = "pdf" if re.fullmatch(_PDF, word, re.IGNORECASE) else "docx" if re.fullmatch(_WORD, word, re.IGNORECASE) else "xlsx"
    before, after = text[: found.start()], text[found.end():]
    # "لـ PDF", "بال pdf": the joining letters before the format are not the question's.
    before = re.sub(r"(?:\s|^)(?:لـ|ل|بـ|ب|بال|كـ|ك|الى|إلى|to|as|in)\s*$", " ", before, flags=re.IGNORECASE)
    rest = " ".join((before + " " + after).replace("؟", " ").replace("?", " ").split()).strip(" .،,:-—")
    rest = _without_request_words(rest).strip(" .،,:-—")
    if _CONVERSATION.search(rest) or (_SUMMARY.search(rest) and len(rest.split()) <= 3):
        return FileRequest(fmt, "conversation")
    if _THAT.match(rest) or len(rest.split()) <= 1:
        return FileRequest(fmt, "last")
    return FileRequest(fmt, "question", rest + "؟")


# -- what goes in it -------------------------------------------------------

@dataclass
class Block:
    kind: str  # heading | paragraph | bullet | table
    text: str = ""
    level: int = 1
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Content:
    title: str
    blocks: list[Block]
    sources: list[str] = field(default_factory=list)
    #: The text of the passages the content rests on — what numbers are checked against.
    evidence: str = ""
    author: str = ""
    note: str = ""
    #: Printed on the file, so a copy can be traced back to its request.
    reference: str = ""


_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CITATION = re.compile(r"\s*\[(\d+(?:\s*[,،]\s*\d+)*)\]")


def plain(text: str) -> str:
    """Markdown emphasis and citation marks removed: a file shows text, not syntax."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return " ".join(text.split())


def parse(markdown: str, keep_citations: bool = True) -> list[Block]:
    blocks: list[Block] = []
    table: list[list[str]] = []

    def flush_table() -> None:
        if table:
            blocks.append(Block("table", rows=[row[:] for row in table]))
            table.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if _TABLE_RULE.match(line) and table:
            continue
        row = _TABLE_ROW.match(line)
        if row:
            table.append([plain(c) for c in row.group(1).split("|")])
            continue
        flush_table()
        if not line.strip():
            continue
        if not keep_citations:
            line = _CITATION.sub("", line)
        heading = re.match(r"^\s*(#{1,4})\s+(.*)$", line)
        bullet = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$", line)
        lead = re.match(r"^\s*\*\*(.+)\*\*\s*(\[[\d,،\s\]\[]*\])?\s*[.。]?\s*$", line)
        if lead and not blocks:
            # The answer's own first line, written in bold: the direct answer.
            blocks.append(Block("lead", plain(lead.group(1))))
        elif heading:
            blocks.append(Block("heading", plain(heading.group(2)), level=len(heading.group(1))))
        elif bullet:
            blocks.append(Block("bullet", plain(bullet.group(1))))
        else:
            blocks.append(Block("paragraph", plain(line)))
    flush_table()
    return blocks


_NUMBER = re.compile(r"\d[\d,.]*")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def number_supported(cell: str, evidence: str) -> bool:
    """Every number in the cell appears in the evidence, written either way."""
    numbers = _NUMBER.findall(cell.translate(_ARABIC_DIGITS))
    if not numbers:
        return True
    haystack = evidence.translate(_ARABIC_DIGITS)
    flat = haystack.replace(",", "")
    for number in numbers:
        clean = number.strip(".,")
        if not clean:
            continue
        if clean not in haystack and clean.replace(",", "") not in flat:
            return False
    return True


# -- the files -------------------------------------------------------------

FONT_DIR = Path(r"C:\Windows\Fonts")
_RTL = re.compile(r"[؀-ۿ]")


def _is_arabic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if _RTL.match(c)) / len(letters) >= 0.3


def render_pdf(content: Content, path: Path) -> None:
    from app.services.file_render import render_pdf as branded

    branded(content, path, rtl=_is_arabic(content.title + " ".join(b.text for b in content.blocks[:5])),
            reference=content.reference)


def render_docx(content: Content, path: Path) -> None:
    from app.services.file_render import render_docx as branded

    branded(content, path, rtl=_is_arabic(content.title + " ".join(b.text for b in content.blocks[:5])),
            reference=content.reference)



def render_xlsx(content: Content, path: Path) -> int:
    """The tables as sheets — or, with none, the content as rows. Returns how many
    numeric cells were not found in the evidence and were marked."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from app.services.file_render import style_sheet

    rtl = _is_arabic(content.title + " ".join(b.text for b in content.blocks[:5]))
    workbook = Workbook()
    workbook.remove(workbook.active)
    header_fill = PatternFill("solid", fgColor="1F3A5F")
    unsure_fill = PatternFill("solid", fgColor="FFF2CC")
    thin = Side(style="thin", color="C9CED6")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    unsupported = 0

    tables = [b.rows for b in content.blocks if b.kind == "table" and b.rows]
    if not tables:
        rows = [["البند" if rtl else "Item", "التفاصيل" if rtl else "Details"]]
        for block in content.blocks:
            if block.kind in ("lead", "paragraph", "bullet", "heading"):
                label, sep, value = block.text.partition(":")
                rows.append([label.strip(), value.strip()] if sep and len(label) <= 60 else [block.text, ""])
        tables = [rows]

    def number(value: str):
        compact = value.translate(_ARABIC_DIGITS).replace(",", "").strip()
        try:
            return float(compact) if re.fullmatch(r"-?\d+(\.\d+)?", compact) else None
        except ValueError:
            return None

    zebra = PatternFill("solid", fgColor="F9FAFC")
    for index, rows in enumerate(tables, 1):
        sheet = workbook.create_sheet(title=(f"جدول {index}" if rtl else f"Table {index}")[:31])
        width = max(len(r) for r in rows)
        start = style_sheet(sheet, content.title, content.author, content.reference, rtl, width)
        for row_index, row in enumerate(rows):
            values = row + [""] * (width - len(row))
            line = start + row_index
            for column, value in enumerate(values, 1):
                parsed = number(value) if row_index else None
                cell = sheet.cell(row=line, column=column, value=parsed if parsed is not None else value)
                cell.border = border
                cell.alignment = Alignment(wrap_text=True, vertical="center",
                                           horizontal="right" if rtl else "left")
                if row_index == 0:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = header_fill
                elif content.evidence and not number_supported(value, content.evidence):
                    cell.fill = unsure_fill
                    cell.comment = Comment("لم يُعثر على هذا الرقم في المصادر — راجعه." if rtl
                                           else "Not found in the sources — check it.", "RAG")
                    unsupported += 1
                elif row_index % 2 == 0:
                    cell.fill = zebra
                if isinstance(cell.value, float):
                    cell.number_format = "#,##0.##"
            sheet.row_dimensions[line].height = 22 if row_index == 0 else None
        sheet.freeze_panes = sheet.cell(row=start + 1, column=1)
        sheet.auto_filter.ref = f"A{start}:{get_column_letter(width)}{start + len(rows) - 1}"
        for column in range(1, width + 1):
            longest = max((len(str(r[column - 1])) if column - 1 < len(r) else 0) for r in rows)
            sheet.column_dimensions[get_column_letter(column)].width = min(60, max(12, longest + 3))

    if content.sources or content.note:
        sheet = workbook.create_sheet(title="المصادر" if rtl else "Sources")
        line = style_sheet(sheet, "المصادر" if rtl else "Sources", content.author, content.reference, rtl, 2)
        if content.note:
            sheet.cell(row=line, column=1, value=content.note).font = Font(color="7A5200")
            line += 2
        for index, source in enumerate(content.sources, 1):
            sheet.cell(row=line, column=1, value=f"[{index}]").font = Font(bold=True, color="1F3A5F")
            sheet.cell(row=line, column=2, value=source)
            line += 1
        sheet.column_dimensions["A"].width = 8
        sheet.column_dimensions["B"].width = 90
    workbook.save(str(path))
    return unsupported


# -- keeping them ----------------------------------------------------------

@dataclass
class StoredFile:
    id: str
    owner: str
    name: str
    format: str
    size: int
    created: str

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES[self.format]


class FileStore:
    """One folder per person. A file is served only to whoever it was made for."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _folder(self, owner: str) -> Path:
        safe = re.sub(r"[^\w-]", "_", owner)
        return self.root / safe

    def path_for(self, owner: str, file_id: str, fmt: str) -> Path:
        folder = self._folder(owner)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{file_id}.{fmt}"

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def record(self, owner: str, file_id: str, name: str, fmt: str) -> StoredFile:
        path = self.path_for(owner, file_id, fmt)
        stored = StoredFile(file_id, owner, name, fmt, path.stat().st_size, datetime.now(UTC).isoformat())
        (path.with_suffix(".json")).write_text(json.dumps(stored.__dict__, ensure_ascii=False), encoding="utf-8")
        return stored

    def open(self, owner: str, file_id: str) -> tuple[StoredFile, Path]:
        if not re.fullmatch(r"[0-9a-f]{32}", file_id):
            raise DocumentNotFoundError("الملف غير موجود.")
        meta = self._folder(owner) / f"{file_id}.json"
        if not meta.exists():
            raise DocumentNotFoundError("الملف غير موجود أو انتهت مدته.")
        stored = StoredFile(**json.loads(meta.read_text(encoding="utf-8")))
        path = self.path_for(owner, file_id, stored.format)
        if stored.owner != owner or not path.exists():
            raise DocumentNotFoundError("الملف غير موجود أو انتهت مدته.")
        return stored, path

    def remove_older_than(self, days: int = KEEP_DAYS) -> int:
        if not self.root.exists():
            return 0
        limit = datetime.now().timestamp() - timedelta(days=days).total_seconds()
        removed = 0
        for path in self.root.glob("*/*"):
            if path.is_file() and path.stat().st_mtime < limit:
                path.unlink(missing_ok=True)
                removed += path.suffix != ".json"
        return removed
