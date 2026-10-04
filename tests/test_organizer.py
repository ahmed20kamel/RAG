"""Files sort themselves into project and type folders — without overruling the uploader.

Checks the three ways a placement is decided — the model's answer, the keyword rules
when the model is unavailable or answers outside the list, and what a folder upload
already said — and that one project is not split into three folders because the model
named it three ways.

Offline: a temporary database and a fake model that returns fixed answers. The documents
are invented.

Run: python tests/test_organizer.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-organizer-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"

from app.models.database import engine, init_database, session_scope  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.services.document_service import DocumentService  # noqa: E402
from app.services.organizer import (  # noqa: E402
    DocumentOrganizer,
    keyword_kind,
    match_project,
    parse_answer,
)

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


class FakeModel:
    def __init__(self, answer: str | Exception):
        self.answer = answer
        self.prompts: list[str] = []

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def add(filename: str, owner: str = "u1", project: str = "", category: str = "General", title: str = "") -> str:
    doc_id = str(uuid.uuid4())
    with session_scope() as session:
        session.add(Document(
            id=doc_id, filename=filename, stored_path="x", content_hash=doc_id, title=title or filename,
            category=category, project=project, folder="", owner_id=owner, status="completed",
            extra_metadata={},
        ))
    return doc_id


def row(doc_id: str) -> Document:
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        session.expunge(doc)
        return doc


INVOICE = "فاتورة رقم 17 — مشروع فيلا الواحة\nالمبلغ المستحق 48,000 درهم عن أعمال الشهر الثالث."
LETTER = "Subject: Delay in site handover\nDear Sir, we refer to your letter dated 3 March regarding the villa works."


def main() -> int:
    init_database()

    print("\n=== 1. keyword rules ===")
    check(keyword_kind("IPC-07.pdf", "", INVOICE) == "دفعة / فاتورة", "an invoice is recognised", keyword_kind("IPC-07.pdf", "", INVOICE))
    check(keyword_kind("letter.pdf", "", LETTER) == "خطاب", "a letter is recognised")
    check(keyword_kind("ملحق عقد المقاولة.pdf", "", "") == "ملحق عقد", "an addendum is not filed as a contract")
    check(keyword_kind("scan001.pdf", "", "نص قصير بلا دلالة") == "", "nothing stands out: no guess")

    print("\n=== 2. one project, one folder ===")
    existing = ["فيلا الواحة", "مستودع المصفح"]
    check(match_project("مشروع فيلا الواحة", existing) == "فيلا الواحة", "a longer name for an existing project reuses it")
    check(match_project("فيلا  الواحة", existing) == "فيلا الواحة", "spacing does not make a new project")
    check(match_project("مدرسة الشامخة", existing) == "مدرسة الشامخة", "a genuinely new project gets its own name")
    check(match_project("", existing) == "", "no project named: none made up")

    print("\n=== 3. reading the model's answer ===")
    check(parse_answer('```json\n{"type": "خطاب", "project": "x"}\n```')["type"] == "خطاب", "code fences are tolerated")
    check(parse_answer("لا أعرف") == {}, "prose without JSON is no answer")

    print("\n=== 4. the model decides, within the list ===")
    model = FakeModel('{"type": "دفعة / فاتورة", "project": "مشروع فيلا الواحة", "date": "2026-03-31", "parties": ["الشركة", "المالك"]}')
    placed = DocumentOrganizer(llm=model).place("inv17.pdf", "", INVOICE, existing)
    check(placed.kind == "دفعة / فاتورة" and placed.by == "model", "type from the model")
    check(placed.project == "فيلا الواحة", "project matched to the existing one", placed.project)
    check(placed.date == "2026-03-31" and placed.parties == ["الشركة", "المالك"], "date and parties kept")
    check("فيلا الواحة" in model.prompts[0], "the owner's existing projects are offered to the model")
    odd = DocumentOrganizer(llm=FakeModel('{"type": "وثيقة سرية", "project": "", "date": "31/3"}')).place("inv17.pdf", "", INVOICE, [])
    check(odd.kind == "دفعة / فاتورة", "a type outside the list falls back to the rules", odd.kind)
    check(odd.date == "", "a date not in YYYY-MM-DD is dropped")
    down = DocumentOrganizer(llm=FakeModel(RuntimeError("model unavailable"))).place("letter.pdf", "", LETTER, [])
    check(down.kind == "خطاب" and down.by == "rules", "model unavailable: the rules still sort it")

    print("\n=== 5. organizing a stored document ===")
    organizer = DocumentOrganizer(llm=FakeModel('{"type": "خطاب", "project": "فيلا الواحة", "date": "2026-03-03", "parties": []}'))
    loose = add("letter-03.pdf")
    organizer.organize(loose, LETTER)
    d = row(loose)
    check((d.project, d.category, d.doc_date) == ("فيلا الواحة", "خطاب", "2026-03-03"), "a single file gets project, type and date", f"{d.project}/{d.category}/{d.doc_date}")
    check(d.extra_metadata.get("organized", {}).get("by") == "model", "how it was placed is recorded")

    from_folder = add("L-001.pdf", project="Villa Gardens", category="Letters")
    organizer.organize(from_folder, LETTER)
    d = row(from_folder)
    check((d.project, d.category) == ("Villa Gardens", "Letters"), "a folder upload keeps the uploader's project and type")
    check(d.doc_date == "2026-03-03", "and only fills what it lacked")

    check(DocumentOrganizer(llm=None, enabled=False).organize(add("x.pdf"), LETTER) is None, "switched off: nothing happens")
    check(DocumentOrganizer(llm=organizer.llm).organize("no-such-id", LETTER) is None, "a deleted document is skipped")

    print("\n=== 6. the folder tree ===")
    add("other.pdf", owner="u2", project="Someone else", category="عقد")
    with session_scope() as session:
        mine = DocumentService.folders(None, session, "u1")
        everyone = DocumentService.folders(None, session, None)
    projects = {f["project"] for f in mine}
    check("Someone else" not in projects, "a user's folders show only their documents")
    check({"project": "فيلا الواحة", "category": "خطاب", "count": 1} in mine, "each folder carries its count", str(mine))
    check(any(f["project"] == "Someone else" for f in everyone), "the administrator's tree has every folder")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        engine.dispose()
        shutil.rmtree(WORKDIR, ignore_errors=True)
    sys.exit(code)
