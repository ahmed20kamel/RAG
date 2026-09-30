"""Telling a document's bookkeeping apart from what the document is about.

Long working files carry two kinds of section. Most of them are the subject — what was
decided, what was paid, what the ruling said. A few are about the file itself: its
version history, the list of artefacts it produced, an index, an integrity card. Both
are legitimate content and both are worth retrieving; they simply answer different
questions.

The failure this module prevents is specific: a question about the current position of
a matter can spend six of its fourteen retrieval slots on the file's own changelog and
its list of generated files, because the question contains the words "file", "update"
and "latest" and those sections are titled with exactly those words. Nearly half the
evidence budget then goes to bookkeeping, and the decisive section never reaches the
answer.

The fix is a re-weighting, not a filter. Nothing is ever excluded:

* asked about the subject, bookkeeping sections lose a fixed amount of score;
* asked about the file itself, they lose nothing and gain a little;
* every adjustment names the cue that caused it, so it can be read in the trace.

The vocabulary below is document-management vocabulary in general — "version history",
"changelog", "table of contents" — not the headings of any particular file. A section
becomes bookkeeping by being titled like bookkeeping, in any document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import normalize

#: Headings that describe the document rather than its subject.
METADATA_HEADING_CUES: tuple[str, ...] = (
    # version and revision history
    "سجل الاصدارات",
    "سجل اصدارات",
    "سجل التعديلات",
    "سجل التغييرات",
    "سجل المراجعات",
    "تاريخ الاصدارات",
    "version history",
    "revision history",
    "change log",
    "changelog",
    "document history",
    # produced artefacts and file inventories
    "الملفات والتقارير المنتجه",
    "الملفات المنتجه",
    "التقارير المنتجه",
    "قائمه الملفات",
    "produced files",
    "generated files",
    "file list",
    "deliverables index",
    # indexes and navigation
    "جدول المحتويات",
    "المحتويات",
    "فهرس",
    "الفهرس",
    "table of contents",
    "index of sections",
    # descriptive metadata about the file
    "بيانات وصفيه",
    "بطاقه سلامه",
    "سلامه سياقيه",
    "contextual integrity",
    "document metadata",
    "file metadata",
    "metadata",
    # reference-only transcripts
    "سجل المحادثات",
    "مرجعي فقط",
    "conversation log",
    "for reference only",
)

#: A heading that talks about *this file* is bookkeeping whatever else it says.
SELF_REFERENCE_CUES: tuple[str, ...] = (
    "هذا الملف",
    "هذه الوثيقه",
    "هذا المستند",
    "this file",
    "this document",
)

#: Questions that genuinely ask about the document as an object. When one of these
#: fires, bookkeeping sections are what the reader wants and must not be pushed down.
METADATA_QUERY_CUES: tuple[str, ...] = (
    "اصدار",
    "الاصدار",
    "اصدارات",
    "الاصدارات",
    "نسخه الملف",
    "نسخه المستند",
    "رقم النسخه",
    "سجل التعديلات",
    "سجل التغييرات",
    "سجل المراجعات",
    "تاريخ الرفع",
    "متى رفع",
    "متى رفعت",
    "من رفع",
    "حجم الملف",
    "عدد الملفات",
    "كم ملف",
    "قائمه الملفات",
    "الملفات المنتجه",
    "التقارير المنتجه",
    "جدول المحتويات",
    "المحتويات",
    "فهرس",
    "بيانات وصفيه",
    "بطاقه سلامه",
    "version",
    "versions",
    "changelog",
    "change log",
    "revision",
    "revisions",
    "uploaded",
    "upload date",
    "file size",
    "how many files",
    "list of files",
    "produced files",
    "generated files",
    "table of contents",
    "metadata",
)

_LETTER = r"[\w؀-ۿ]"


def _pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(normalize(c)) for c in sorted(cues, key=len, reverse=True)
    )
    return re.compile(f"(?<!{_LETTER})(?:{alternatives})(?!{_LETTER})")


HEADING_PATTERN = _pattern(METADATA_HEADING_CUES)
SELF_REFERENCE_PATTERN = _pattern(SELF_REFERENCE_CUES)
QUERY_PATTERN = _pattern(METADATA_QUERY_CUES)


@dataclass(frozen=True, slots=True)
class EvidenceClass:
    """What a passage is, and why it was judged so."""

    is_metadata: bool = False
    cue: str = ""

    def describe(self) -> str:
        return f"قسم إداري عن الملف نفسه («{self.cue}»)" if self.is_metadata else ""


def classify_section(section_path: str, heading: str = "") -> EvidenceClass:
    """Whether a passage belongs to the document's bookkeeping.

    Judged from the heading and the breadcrumb only, never from the body. A section
    about the ruling may well mention which version of the file recorded it; that does
    not make the ruling bookkeeping, and reading the body would make it look like it.
    """
    text = normalize(f"{section_path} {heading}")
    if not text.strip():
        return EvidenceClass()

    match = HEADING_PATTERN.search(text)
    if match:
        return EvidenceClass(is_metadata=True, cue=match.group(0))

    match = SELF_REFERENCE_PATTERN.search(text)
    if match:
        return EvidenceClass(is_metadata=True, cue=match.group(0))

    return EvidenceClass()


def query_wants_metadata(question: str) -> EvidenceClass:
    """Whether the question is about the document as an object.

    Deliberately narrow. The bare words "file", "document" and "update" appear in
    ordinary subject questions constantly — "what is the latest update on the claim" is
    not a question about a changelog — so only wordings that name a document property
    count: a version, a revision, an upload, an inventory, an index.
    """
    match = QUERY_PATTERN.search(normalize(question))
    return EvidenceClass(is_metadata=bool(match), cue=match.group(0) if match else "")
