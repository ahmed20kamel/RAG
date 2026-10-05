"""What an uploaded file actually is, decided from its bytes.

An extension is a claim the uploader makes, and a browser's MIME type is a claim the
browser makes; neither is evidence. The cost of believing them is not abstract — handing
a renamed archive to a parser that trusts the name is how a file gets opened by code
written for something else entirely.

So the bytes decide, and the extension only has to agree. A `.xlsx` that is really a PDF
is rejected rather than quietly parsed as a PDF, because the mismatch itself is the
interesting fact: either something went wrong, or somebody is trying something.

No libmagic. The four formats here are distinguishable from their first bytes, and an
Office file's identity is settled by looking inside the ZIP container rather than by
guessing from the extension it arrived with.
"""

from __future__ import annotations

import io
import logging
import zipfile

from app.core.domain import FileType

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

#: The part every OOXML package names to say what it is. Present in any valid file of
#: that type, wherever the rest of the package happens to be laid out.
OOXML_MARKERS: tuple[tuple[str, FileType], ...] = (
    ("word/document.xml", FileType.DOCX),
    ("xl/workbook.xml", FileType.XLSX),
)

#: Legacy binary Office. Detected only so the refusal can say something useful — these
#: are a different format from their modern namesakes, not an older version of one.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

EXTENSION_HINTS: dict[str, FileType] = {
    ".md": FileType.MARKDOWN,
    ".markdown": FileType.MARKDOWN,
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".xlsx": FileType.XLSX,
    ".xlsm": FileType.XLSX,
    ".png": FileType.IMAGE,
    ".jpg": FileType.IMAGE,
    ".jpeg": FileType.IMAGE,
    ".tif": FileType.IMAGE,
    ".tiff": FileType.IMAGE,
    ".bmp": FileType.IMAGE,
    ".webp": FileType.IMAGE,
}

#: Photos and scans saved as pictures. Any of these signatures is an image; which kind
#: does not matter, since every one is read the same way.
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"BM")


class DetectionResult:
    """What the bytes say, whether the extension agreed, and whether the file is broken."""

    __slots__ = ("file_type", "claimed", "agrees", "detail", "corrupt")

    def __init__(
        self, file_type: str, claimed: str, detail: str = "", corrupt: bool = False
    ) -> None:
        self.file_type = file_type
        self.claimed = claimed
        self.detail = detail
        # A file that opens with the right signature but will not read is damaged, not
        # the wrong format. Telling someone their .xlsx is "an unsupported format" sends
        # them to convert a file that needs replacing instead.
        self.corrupt = corrupt
        # Unknown-by-content is not a disagreement: a Markdown file has no signature, so
        # plain text that claims to be Markdown is taken at its word.
        self.agrees = file_type == claimed or (
            file_type == FileType.UNKNOWN and claimed == FileType.MARKDOWN
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"DetectionResult({self.file_type!r}, claimed={self.claimed!r}, agrees={self.agrees})"


def detect(content: bytes, extension: str) -> DetectionResult:
    """Identify `content`, and report whether `extension` was telling the truth."""
    claimed = EXTENSION_HINTS.get(extension.lower(), FileType.UNKNOWN)
    file_type, corrupt = _sniff(content)
    return DetectionResult(file_type, claimed, _describe(content, corrupt), corrupt)


def _sniff(content: bytes) -> tuple[str, bool]:
    """The format, and whether the container was damaged on the way in."""
    if content.startswith(PDF_MAGIC):
        return FileType.PDF, False
    if content.startswith(ZIP_MAGIC):
        return _inside_zip(content)
    if content.startswith(IMAGE_MAGIC) or (content[:4] == b"RIFF" and content[8:12] == b"WEBP"):
        return FileType.IMAGE, False
    return FileType.UNKNOWN, False


def _inside_zip(content: bytes) -> tuple[str, bool]:
    """Which OOXML package this is, read from the archive's own listing.

    Only names are read. Nothing is extracted, nothing is decompressed beyond the central
    directory, so a zip bomb has nothing to detonate against here.

    An archive that will not open at all is reported as damaged rather than as an
    unknown format: the ZIP signature is already evidence of what it was meant to be.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        logger.info("Not a readable archive: %s", exc)
        return FileType.UNKNOWN, True

    for marker, file_type in OOXML_MARKERS:
        if marker in names:
            return file_type, False
    # A readable archive that is not an Office package is genuinely the wrong format.
    return FileType.UNKNOWN, False


def _describe(content: bytes, corrupt: bool = False) -> str:
    """A short, safe note for the refusal message. Never echoes file content."""
    if content.startswith(OLE_MAGIC):
        return "صيغة Office القديمة (‎.doc/.xls‎) — احفظ الملف بصيغة ‎.docx/.xlsx‎."
    if corrupt:
        return "الملف تالف أو غير مكتمل — أعد تصديره ثم ارفعه مرة أخرى."
    if content.startswith(ZIP_MAGIC):
        return "أرشيف مضغوط لا يحتوي على مستند Word أو Excel."
    return ""


def macro_enabled(content: bytes) -> bool:
    """Whether an OOXML package carries a macro project.

    Macros are never executed here — no code in this system opens a VBA project, and the
    parsers read XML parts only. This exists so the fact can be recorded and shown, not
    because anything would otherwise run.
    """
    if not content.startswith(ZIP_MAGIC):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return any(name.endswith("vbaProject.bin") for name in archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
