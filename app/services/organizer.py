"""Files sorted into folders on their own: project, document type, date and parties.

After a document is indexed, its first pages are read once more to decide where it
belongs. A folder upload already said that, so its project and folder are kept as they
are; what the uploader did not say is filled in. A single file dropped on its own gets
everything from its content.

Project names are matched against the owner's existing projects before a new one is
made, because the model names the same villa three ways across three letters and a
library with three folders for one project is worse than one with none.

The model is asked once per document. When it is unavailable, or answers with something
that is not one of the known types, keyword rules decide the type and the project is left
for the uploader. Never fatal: a document that cannot be sorted stays where it was.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.text import normalize
from app.models.database import session_scope
from app.models.document import Document

logger = logging.getLogger(__name__)

#: The folders a construction company's paperwork falls into, each with the words that
#: give it away in Arabic and English. Order matters for the keyword fallback: the more
#: specific kinds come before the ones whose words they share ("ملحق عقد" before "عقد").
DOCUMENT_TYPES: list[tuple[str, tuple[str, ...]]] = [
    ("ملحق عقد", ("ملحق عقد", "ملحق العقد", "addendum", "contract amendment")),
    ("أمر تغيير", ("أمر تغيير", "اوامر تغيير", "أعمال إضافية", "variation order", "variation", "change order")),
    ("تمديد مدة", ("تمديد مدة", "تمديد المدة", "extension of time", "eot")),
    ("جدول كميات", ("جدول الكميات", "جدول كميات", "bill of quantities", "boq")),
    ("عرض سعر", ("عرض سعر", "عرض أسعار", "quotation", "price offer")),
    ("دفعة / فاتورة", ("فاتورة", "مستخلص", "دفعة", "invoice", "payment certificate", "ipc", "payment")),
    ("محضر اجتماع", ("محضر اجتماع", "محضر", "minutes of meeting", "meeting minutes", "mom")),
    ("طلب معاينة / استفسار", ("طلب معاينة", "طلب استفسار", "inspection request", "request for information", "rfi")),
    ("مخطط", ("مخطط", "drawing", "layout plan")),
    ("مستند قضائي", ("محكمة", "حكم", "دعوى", "مذكرة", "خبير", "court", "judgment", "lawsuit")),
    ("تقرير", ("تقرير", "report")),
    ("خطاب", ("خطاب", "كتاب", "الموضوع", "subject:", "dear", "letter")),
    ("عقد", ("عقد", "اتفاقية", "contract", "agreement")),
]
TYPE_LABELS = [label for label, _ in DOCUMENT_TYPES] + ["أخرى"]
#: Characters of the document shown to the model: the letterhead, parties and subject
#: are on the first page; more only costs time on a shared graphics card.
EXCERPT_CHARS = 2500
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(slots=True)
class Placement:
    kind: str = ""
    project: str = ""
    date: str = ""
    parties: list[str] = field(default_factory=list)
    by: str = "rules"   # "model" or "rules"


def keyword_kind(filename: str, title: str, text: str) -> str:
    """The type the words point to, or "" when none stands out. Filename and title weigh
    most: people name a file after what it is."""
    head = normalize(f"{filename} {title}")
    # The first lines carry the letterhead and the subject: "فاتورة رقم 17" there says
    # more than the same word anywhere further down.
    top = normalize(text[:200])
    body = normalize(text[:EXCERPT_CHARS])
    best, best_score = "", 0
    for label, words in DOCUMENT_TYPES:
        score = 0
        for word in words:
            w = normalize(word)
            if w and w in head:
                score += 3
            elif w and w in top:
                score += 2
            elif w and w in body:
                score += 1
        if score > best_score:
            best, best_score = label, score
    return best if best_score >= 2 else ""


def _tokens(name: str) -> set[str]:
    return {t for t in normalize(name).split() if len(t) > 1}


def match_project(name: str, existing: list[str]) -> str:
    """An existing project this name refers to, or the name itself when it is new."""
    name = " ".join(name.split()).strip(" .-_")
    if not name:
        return ""
    words = _tokens(name)
    best, best_overlap = "", 0.0
    for candidate in existing:
        other = _tokens(candidate)
        if not words or not other:
            continue
        overlap = len(words & other) / min(len(words), len(other))
        if overlap > best_overlap:
            best, best_overlap = candidate, overlap
    return best if best_overlap >= 0.6 else name[:120]


def parse_answer(raw: str) -> dict:
    """The JSON object in a model's answer, tolerating code fences and stray prose."""
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


SYSTEM_PROMPT = (
    "أنت مصنّف مستندات لشركة مقاولات. تقرأ بداية مستند وتحدد نوعه ومشروعه. "
    "أجب بكائن JSON واحد فقط بلا أي شرح."
)


def user_prompt(filename: str, excerpt: str, existing: list[str]) -> str:
    projects = "، ".join(existing) if existing else "لا يوجد"
    return (
        f"اسم الملف: {filename}\n\n"
        f"بداية المستند:\n{excerpt}\n\n"
        f"الأنواع المسموحة (اختر واحدًا كما هو): {'، '.join(TYPE_LABELS)}\n"
        f"مشاريع هذا المستخدم الحالية: {projects}\n\n"
        "أعد JSON بهذه المفاتيح:\n"
        '{"type": "<نوع من القائمة>", '
        '"project": "<اسم المشروع كما في القائمة إن كان المستند يخص أحدها، وإلا اسم قصير جديد من المستند، أو فارغ إن لم يظهر مشروع>", '
        '"date": "<تاريخ المستند YYYY-MM-DD أو فارغ>", '
        '"parties": ["<الأطراف الرئيسية، ثلاثة على الأكثر>"]}'
    )


class DocumentOrganizer:
    def __init__(self, llm=None, enabled: bool = True) -> None:
        self.llm = llm
        self.enabled = enabled

    def place(self, filename: str, title: str, text: str, existing: list[str]) -> Placement:
        """Where a document belongs. Asks the model, falls back to the words."""
        fallback = Placement(kind=keyword_kind(filename, title, text) or "أخرى")
        if self.llm is None:
            return fallback
        try:
            answer = parse_answer(self.llm.chat(SYSTEM_PROMPT, user_prompt(filename, text[:EXCERPT_CHARS], existing)))
        except Exception as exc:  # noqa: BLE001 - the model is optional here
            logger.info("Organizer fell back to keyword rules for %s: %s", filename, exc)
            return fallback
        kind = str(answer.get("type", "")).strip()
        if kind not in TYPE_LABELS:
            kind = fallback.kind
        project = match_project(str(answer.get("project", "") or ""), existing)
        date = str(answer.get("date", "") or "").strip()
        parties = [str(p).strip()[:120] for p in (answer.get("parties") or []) if str(p).strip()][:3]
        return Placement(kind=kind, project=project, date=date if DATE.match(date) else "", parties=parties, by="model")

    def organize(self, document_id: str, text: str) -> Placement | None:
        """Sort one indexed document into its folder. Keeps whatever the uploader set."""
        if not self.enabled:
            return None
        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is None:
                return None
            filename, title, owner = document.filename, document.title, document.owner_id
            has_project = bool(document.project)
            has_kind = bool(document.category) and document.category not in ("General", "عام")
            query = select(Document.project).distinct().where(Document.project != "")
            query = query.where(Document.owner_id == owner) if owner else query.where(Document.owner_id.is_(None))
            existing = [p for p in session.scalars(query) if p]

        placement = self.place(filename, title, text, existing)

        with session_scope() as session:
            document = session.get(Document, document_id)
            if document is None:   # deleted while it was being read
                return None
            if not has_kind and placement.kind:
                document.category = placement.kind
            if not has_project and placement.project:
                document.project = placement.project
            if not document.doc_date and placement.date:
                document.doc_date = placement.date
            document.extra_metadata = {
                **(document.extra_metadata or {}),
                "organized": {
                    "kind": placement.kind, "project": placement.project, "date": placement.date,
                    "parties": placement.parties, "by": placement.by,
                    "kept_upload_folder": has_project or has_kind,
                },
            }
        logger.info("Organized %s: %s / %s (%s)", filename, placement.project or "-", placement.kind, placement.by)
        return placement
