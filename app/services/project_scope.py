"""Which project a question is about, when it names one — and which project a source is.

Measured on the company's files: asked for "the insulation total in Majed Al Hammadi's
revised BOQ", the system found that BOQ and answered that no document was "attributed to
Majed Al Hammadi" — the spreadsheet never names its owner; only its project does. And
asked about "جدول الكميات المعدل لفيلا ماجد الحمادي", it searched another villa's.

Two things follow:

* every source is shown with its project, so the model can tie a file that never names
  its owner to the owner asked about;
* a question that names a project — by an owner, a party or a code its files carry — is
  searched among that project's files (and among everything, if that finds nothing).

A project is known by distinctive words only: an owner's family name, a party, a code.
Words many projects share — first names, "villa", "شركة ال يافور" — name none of them.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict

from app.core.text import normalize

logger = logging.getLogger(__name__)

#: A word whose project files' text carries it in more projects than this names none of
#: them: "تنفيذ" is in every contract; "الحمادي" is in Majed's files and few others.
MAX_PROJECTS_PER_WORD = 3
MIN_WORD = 4

#: Words that name no project: company boilerplate, titles, places every project shares,
#: document kinds, and first names.
_COMMON = {
    "شركه", "شركة", "اليافور", "يافور", "للنقليات", "والمقاولات", "المقاولات", "للمقاولات", "العامه", "العامة",
    "alyafour", "yafour", "general", "contracting", "transport", "company", "llc", "l.l.c",
    "ابوظبي", "ظبي", "dhabi", "abu", "مدينه", "مدينة", "city", "الرياض", "riyadh", "madinat", "الامارات", "uae",
    "villa", "فيلا", "فيلة", "private", "residential", "project", "مشروع", "المشروع", "الخاصه", "سكنيه", "خاصه",
    "المالك", "owner", "client", "contractor", "المقاول", "consultant", "الاستشاري", "consultancy", "engineering",
    "abdulla", "abdullah", "عبدالله", "mohamed", "mohammed", "محمد", "ahmed", "احمد", "ali", "علي", "saeed",
    "سعيد", "khalid", "خالد", "rashid", "راشد", "sultan", "سلطان", "hasan", "حسن", "hassan", "salem", "سالم",
    "bin", "بن", "mubarak", "مبارك", "khamis", "خميس", "eissa", "عيسي", "عيسى", "jumaa", "جمعه",
    "contract", "boq", "offer", "drawing", "signed", "final", "revised", "summary", "payments", "payment",
    "عقد", "ملحق", "جدول", "الكميات", "تقرير", "مذكره", "حكم", "محضر", "جلسه", "عرض", "سعر",
    "plot", "قطعه", "قطعة", "sector", "حوض", "street", "شارع",
    "المعدل", "معدل", "المعدله", "consulting", "constructions", "developer", "municipalities", "products",
    "home", "automation", "instead", "international", "انترناشيونال", "العقاري", "والنقل", "والزجاج", "والالمنيوم",
}


def _common() -> set[str]:
    from app.services.document_scope import _DOCUMENT_KIND_WORDS

    return {normalize(w).lower() for w in _COMMON | _DOCUMENT_KIND_WORDS}


def _words(text: str) -> set[str]:
    words = set()
    for raw in normalize(text or "").lower().replace("_", " ").replace("-", " ").split():
        word = raw.strip(".,:;()[]«»\"'/")
        if len(word) >= MIN_WORD and not word.isdigit() and word not in _COMMON_WORDS:
            words.add(word)
    return words


_COMMON_WORDS = _common()


class ProjectScope:
    """Project of each document, and the projects a question names."""

    def __init__(self, loader=None, refresh_seconds: float = 600.0) -> None:
        self._loader = loader or _rows_from_db
        self._refresh_seconds = refresh_seconds
        self._loaded_at = 0.0
        self._lock = threading.Lock()
        self._project_of: dict[str, str] = {}
        self._documents_of: dict[str, list[str]] = {}
        self._projects_of_word: dict[str, set[str]] = {}

    def _refresh(self) -> None:
        if time.monotonic() - self._loaded_at <= self._refresh_seconds:
            return
        with self._lock:
            if time.monotonic() - self._loaded_at <= self._refresh_seconds:
                return
            try:
                rows = self._loader()
            except Exception:  # noqa: BLE001 - scoping is a refinement; without it, search everything
                logger.exception("Could not read the projects for scoping")
                rows = []
            project_of: dict[str, str] = {}
            documents_of: dict[str, list[str]] = defaultdict(list)
            words_of: dict[str, set[str]] = defaultdict(set)
            text_of: dict[str, list[str]] = defaultdict(list)
            for document_id, filename, project, parties, text in rows:
                if not project:
                    continue
                project_of[document_id] = project
                documents_of[project].append(document_id)
                words_of[project] |= _words(project) | _words(filename.rsplit(".", 1)[0])
                for party in parties:
                    words_of[project] |= _words(party)
                text_of[project].append(normalize(" ".join([project, filename, *parties, text])).lower())
            texts = {project: " ".join(parts) for project, parts in text_of.items()}
            # A project's name words, but each counted by every project whose files say it:
            # a name is a word few projects' files carry.
            projects_of_word: dict[str, set[str]] = {}
            for word in set().union(*words_of.values()) if words_of else set():
                projects_of_word[word] = {project for project, text in texts.items() if word in text}
            self._project_of = project_of
            self._documents_of = dict(documents_of)
            self._projects_of_word = {w: p for w, p in projects_of_word.items() if len(p) <= MAX_PROJECTS_PER_WORD}
            self._loaded_at = time.monotonic()

    def project(self, document_id: str) -> str:
        self._refresh()
        return self._project_of.get(document_id, "")

    def resolve(self, question: str, among: list[str] | None = None) -> tuple[list[str], list[str]]:
        """The documents of the project(s) the question names, and those projects' names.
        Empty when it names none — or names so many that naming is not narrowing."""
        self._refresh()
        hits: dict[str, int] = defaultdict(int)
        for word in _words(question):
            for project in self._projects_of_word.get(word, ()):
                hits[project] += 1
        if not hits:
            return [], []
        best = max(hits.values())
        projects = sorted(p for p, n in hits.items() if n == best)
        if len(projects) > MAX_PROJECTS_PER_WORD:
            return [], []
        allowed = set(among) if among is not None else None
        documents = [d for p in projects for d in self._documents_of.get(p, []) if allowed is None or d in allowed]
        return documents, projects

    def without_names(self, question: str, projects: list[str]) -> str:
        """The question without the words that named these projects.

        Searched among a project's own files, its owner's name only adds weight to the
        passages that repeat it — contract clauses — over the row that answers: measured,
        "بند الجسور في BOQ فيلا ماجد الحمادي" ranked sixteen contract passages above the
        BOQ row "الجسور | م3 | 72 | 1250 | 90000", which never names the owner.
        """
        self._refresh()
        names = {w for w, owners in self._projects_of_word.items() if owners & set(projects)}
        kept = [raw for raw in question.split()
                if normalize(raw).lower().strip(".,:;()[]«»\"'/؟?") not in names]
        return " ".join(kept) if len(kept) >= 2 else question


def _rows_from_db() -> list[tuple[str, str, str, list[str], str]]:
    from sqlalchemy import select

    from app.models.database import session_scope
    from app.models.document import Document
    from app.models.knowledge import ChunkRecord

    with session_scope() as db:
        found = db.execute(
            select(Document.id, Document.filename, Document.project, Document.extra_metadata)
            .where(Document.status == "completed")
        ).all()
        texts: dict[str, list[str]] = defaultdict(list)
        for document_id, content in db.execute(select(ChunkRecord.document_id, ChunkRecord.content)).all():
            texts[str(document_id)].append(content or "")
    rows = []
    for document_id, filename, project, extra in found:
        try:
            meta = extra if isinstance(extra, dict) else json.loads(extra or "{}")
        except (TypeError, ValueError):
            meta = {}
        parties = (meta.get("organized") or {}).get("parties") or []
        rows.append((str(document_id), filename or "", project or "", [str(p) for p in parties],
                     " ".join(texts.get(str(document_id), []))))
    return rows
