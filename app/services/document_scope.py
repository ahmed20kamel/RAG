"""Which document a question is about, when the question names one.

Search runs over every indexed document. That is right for "ما مهلة إخطار المطالبة؟"
and wrong for "اقرأ ملف PROJECT_MIGRATION_SUMMARY وأجب منه فقط": asked exactly that, with
two documents indexed whose names both contain those words — one a construction
lawsuit, the other an employment matter — the system searched both and answered with
one matter's ruling beside the other's settlement, as though they were one case.

So a question that names a file is answered from that file, and a name that fits more
than one file is answered with a question, never with a blend:

* the name as the documents carry it is matched — the filename without its extension,
  with "_", "-" and "." read as spaces, and a title when no other document shares it;
* every word of that name must appear in the question, in order — naming the file,
  not merely touching one of its words ("FIDIC" in a question is not
  "FIDIC_Claims_Procedure");
* a version the question states ("v1_24", "v1.24") or the full filename with its
  extension picks one document out of several that share a base name;
* otherwise, more than one match is ambiguous, and the reply lists the candidates.

Deterministic, no model call, and a question that names no document is untouched.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.text import normalize

logger = logging.getLogger(__name__)

#: Everything that is not a letter or a digit separates — "_", ".", "-", and the
#: punctuation a question puts around a name: "PROJECT_SUMMARY.md:" is still that file.
_SEPARATORS = re.compile(r"[^0-9A-Za-z؀-ۿ]+")
#: Tokens that distinguish versions of one document rather than name it.
_VERSION = re.compile(r"^(?:v\d+|\d+|rev\d*|r\d+)$")
#: A name shorter than this is too generic to count as naming a document by itself.
#: Words a picked list line or a bare reference carries that ask for nothing.
_REFERENCE_FILLER = {"رفع", "الملف", "ملف", "file", "uploaded", "project", "unified", "stream", "md"}
MIN_NAME_TOKENS = 2
MIN_SINGLE_TOKEN = 6


def _tokens(text: str) -> list[str]:
    return [t for t in _SEPARATORS.split(normalize(text).lower()) if t]


def _contains(haystack: list[str], needle: list[str]) -> bool:
    """`needle` appears in `haystack` as a contiguous run."""
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[i:i + width] == needle for i in range(len(haystack) - width + 1))


@dataclass(frozen=True)
class KnownDocument:
    document_id: str
    filename: str
    title: str
    uploaded: str
    #: The filename without extension, tokenised.
    name: tuple[str, ...]
    #: The same without version tokens — what several versions share.
    base: tuple[str, ...]
    extension: str


@dataclass
class ScopeDecision:
    """What the question said about which document to read."""

    #: The single document the question named, when it named exactly one.
    document: KnownDocument | None = None
    #: The documents a name fitted, when it fitted more than one.
    candidates: list[KnownDocument] = field(default_factory=list)
    #: The words of the question that named a document.
    named: str = ""

    @property
    def ambiguous(self) -> bool:
        return self.document is None and len(self.candidates) > 1

    def choices(self, question: str) -> list[tuple[str, str]]:
        """(label, question) per candidate: the asker's own question, naming that file.

        Asked "which one do you mean?", people copy a line of the list and send it — and
        the line arrived alone, without the question it was meant to answer. A choice
        that resends the original question, now naming one file, cannot lose it.
        """
        base = max((d.base for d in self.candidates), key=len)
        pattern = re.compile(
            r"(?i)" + r"[^0-9A-Za-z؀-ۿ]+".join(re.escape(t) for t in base)
            + r"(?:[^0-9A-Za-z؀-ۿ]+(?:v?\d+))*(?:\.[A-Za-z]{2,5})?"
        )
        out = []
        for doc in self.candidates:
            rewritten, count = pattern.subn(doc.filename, question, count=1)
            if not count:
                rewritten = f"{question.rstrip()} (الملف: {doc.filename})"
            out.append((f"{doc.filename} — {doc.uploaded}", rewritten))
        return out

    def clarification(self, english: bool = False) -> str:
        """The reply to an ambiguous name: the candidates, and how to choose."""
        if english:
            lines = [f'"{self.named}" matches more than one document. Which one do you mean?']
            lines += [f"{i}) {d.filename} — uploaded {d.uploaded}" + (f" — {d.title}" if d.title else "")
                      for i, d in enumerate(self.candidates, 1)]
            lines.append("Ask again with the full file name, and I will answer from that file only.")
            return "\n".join(lines)
        lines = [f"الاسم «{self.named}» يطابق أكثر من ملف، ولن أدمج ملفين في إجابة واحدة. أيّهما تقصد؟"]
        lines += [f"{i}) {d.filename} — رُفع {d.uploaded}" + (f" — {d.title}" if d.title else "")
                  for i, d in enumerate(self.candidates, 1)]
        lines.append("اضغط على الملف المقصود، أو أعد السؤال باسمه كاملًا، وسأجيب منه وحده.")
        return "\n".join(lines)

    def is_bare_reference(self, question: str) -> bool:
        """A message that names the file and asks nothing — a picked line of the list.

        What is left once the file's name, title, dates and dashes are taken away is at
        most a word or two, and no question word.
        """
        if self.document is None:
            return False
        rest = normalize(question).lower()
        for piece in (self.document.filename, self.document.title, " ".join(self.document.name)):
            if piece:
                rest = rest.replace(normalize(piece).lower(), " ")
        rest = re.sub(r"\d{1,2}/\d{1,2}/\d{4}|[^\w؀-ۿ]+|_", " ", rest)
        words = [w for w in rest.split() if len(w) > 2 and w not in _REFERENCE_FILLER]
        return len(words) <= 1


class DocumentScope:
    """Resolves document names in questions against the indexed documents."""

    def __init__(self, loader=None, refresh_seconds: float = 30.0) -> None:
        self._loader = loader or _documents_from_db
        self._refresh_seconds = refresh_seconds
        self._documents: list[KnownDocument] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def documents(self) -> list[KnownDocument]:
        if time.monotonic() - self._loaded_at > self._refresh_seconds:
            with self._lock:
                if time.monotonic() - self._loaded_at > self._refresh_seconds:
                    try:
                        self._documents = self._loader()
                    except Exception:  # noqa: BLE001
                        # Scoping is a refinement. If the list cannot be read, the
                        # question is answered across everything, exactly as before.
                        logger.exception("Could not read the document list for scoping")
                    self._loaded_at = time.monotonic()
        return self._documents

    def resolve(self, question: str) -> ScopeDecision:
        documents = self.documents()
        if not documents:
            return ScopeDecision()
        words = _tokens(question)
        titles_seen: dict[tuple[str, ...], int] = {}
        for doc in documents:
            key = tuple(_tokens(doc.title))
            titles_seen[key] = titles_seen.get(key, 0) + 1

        # 1. A full filename, extension included, names exactly that file.
        exact = [d for d in documents if _contains(words, list(d.name) + ([d.extension] if d.extension else []))
                 and d.extension]
        if len(exact) == 1:
            return ScopeDecision(document=exact[0], named=exact[0].filename)

        # 2. The base name — what versions of a document share.
        matched = [d for d in documents if _names(d.base) and _contains(words, list(d.base))]
        # A title that no other document carries names its document too.
        for doc in documents:
            title = tuple(_tokens(doc.title))
            if doc not in matched and len(title) >= 3 and titles_seen.get(title) == 1 and _contains(words, list(title)):
                matched.append(doc)
        if not matched:
            return ScopeDecision()

        named = " ".join(max((d.base for d in matched), key=len))
        if len(matched) == 1:
            return ScopeDecision(document=matched[0], named=named)

        # 3. A version the question states picks one of several.
        versioned = [d for d in matched if len(d.name) > len(d.base) and _contains(words, list(d.name))]
        if len(versioned) == 1:
            return ScopeDecision(document=versioned[0], named=versioned[0].filename)

        # 4. A longer name that contains a shorter one, both fully present: the longer
        # is the more specific thing the question named.
        longest = max(len(d.base) for d in matched)
        specific = [d for d in matched if len(d.base) == longest]
        if len(specific) == 1 and all(_contains(list(specific[0].base), list(d.base)) for d in matched):
            if any(len(d.base) < longest for d in matched):
                return ScopeDecision(document=specific[0], named=named)

        return ScopeDecision(candidates=sorted(matched, key=lambda d: d.uploaded), named=named)


def _names(base: tuple[str, ...]) -> bool:
    """Whether a name is specific enough that stating it means naming the file."""
    return len(base) >= MIN_NAME_TOKENS or (len(base) == 1 and len(base[0]) >= MIN_SINGLE_TOKEN)


def known(document_id: str, filename: str, title: str = "", uploaded: str = "") -> KnownDocument:
    stem, _, extension = filename.rpartition(".")
    if not stem:
        stem, extension = filename, ""
    name = tuple(_tokens(stem))
    base = tuple(t for t in name if not _VERSION.match(t)) or name
    return KnownDocument(
        document_id=document_id, filename=filename, title=(title or "").strip()[:80],
        uploaded=uploaded, name=name, base=base, extension=normalize(extension).lower(),
    )


def _documents_from_db() -> list[KnownDocument]:
    from app.models.database import session_scope
    from app.models.document import Document

    with session_scope() as db:
        rows = db.execute(
            select(Document.id, Document.filename, Document.title, Document.uploaded_at)
            .where(Document.status == "completed")
        ).all()
    return [
        known(str(row.id), row.filename, row.title,
              row.uploaded_at.strftime("%d/%m/%Y") if row.uploaded_at else "")
        for row in rows
    ]
