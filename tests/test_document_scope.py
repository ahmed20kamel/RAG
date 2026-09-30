"""A question that names a file is answered from that file — or asks which one.

Two documents sharing a name were searched together and answered as one matter: one a
construction lawsuit, the other an employment file, both carrying the same base name.
This checks the resolver on the names people actually write, and the pipeline's two
responses to it: scoping to one document, and asking instead of blending.

Deterministic and offline: the document list is supplied, not read from a database.

Run: python tests/test_document_scope.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.schemas.chat import ChatRequest, ChatResponse  # noqa: E402
from app.services.document_scope import DocumentScope, known  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.rag_service import RagService  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


SAME_TITLE = "PROJECT SUMMARY — Unified-Stream Project"
DOCUMENTS = [
    known("lawsuit", "PROJECT_SUMMARY.md", SAME_TITLE, "20/09/2026"),
    known("employment", "PROJECT_SUMMARY_v1_24.md", SAME_TITLE, "29/09/2026"),
    known("fidic", "FIDIC_Claims_Procedure.md", "FIDIC Claims Procedure", "20/09/2026"),
    known("insurance", "دليل_العمل_مطالبات_التأمين.md", "دليل العمل", "18/09/2026"),
    known("short", "CP4-MC.pdf", "", "21/09/2026"),
    known("unique", "notes.md", "سجل الاجتماعات الأسبوعية للمشروع", "21/09/2026"),
]
SCOPE = DocumentScope(loader=lambda: DOCUMENTS, refresh_seconds=3600)


def resolved(question: str) -> str:
    decision = SCOPE.resolve(question)
    if decision.ambiguous:
        return "ambiguous:" + ",".join(d.document_id for d in decision.candidates)
    return decision.document.document_id if decision.document else "all"


def names_are_resolved() -> None:
    print("\n-- 1. a named file, a shared name, and no name at all --")
    cases = {
        "اقرأ ملف PROJECT_SUMMARY المرفوع، وأجب اعتمادًا عليه فقط": "ambiguous:lawsuit,employment",
        "من ملف PROJECT_SUMMARY_v1_24 ما آخر مرحلة؟": "employment",
        "من project summary v1.24 ما المطلوب؟": "employment",
        "من PROJECT_SUMMARY.md ما صفة الجهة الثانية؟": "lawsuit",
        "من ملف PROJECT_SUMMARY.md: ما رقم هاتف المختص؟": "lawsuit",      # a colon after the name
        "(PROJECT_SUMMARY_v1_24.md) ما آخر مرحلة؟": "employment",          # brackets around it
        "حسب FIDIC_Claims_Procedure ما مهلة الإخطار؟": "fidic",
        "في دليل العمل مطالبات التأمين ما الخطوة الأولى؟": "insurance",
        "في سجل الاجتماعات الأسبوعية للمشروع ماذا تقرر؟": "unique",
    }
    for question, wanted in cases.items():
        got = resolved(question)
        check(got == wanted, f"{wanted:30} ← {question[:50]}", f"got {got}")


def touching_a_word_is_not_naming() -> None:
    print("\n-- 2. mentioning a word of a name is not naming the file --")
    for question in (
        "ما إجراءات المطالبات وفق FIDIC؟",       # one word of a three-word name
        "ما السقف الأقصى لغرامة التأخير؟",       # nothing
        "ما ملخص المشروع؟",                      # a title shared by two documents
        "ما حالة CP4؟",                          # a name too short to be specific
    ):
        check(resolved(question) == "all", f"all documents ← {question}")


def the_ambiguity_reply() -> None:
    print("\n-- 3. an ambiguous name is answered with the candidates --")
    decision = SCOPE.resolve("اقرأ ملف PROJECT_SUMMARY المرفوع")
    text = decision.clarification()
    check("PROJECT_SUMMARY.md" in text and "PROJECT_SUMMARY_v1_24.md" in text,
          "both files are listed", text[:160])
    check("لن أدمج" in text, "and it says it will not blend them")
    check(decision.candidates[0].document_id == "lawsuit", "older first")


class _Retriever:
    def __init__(self) -> None:
        self.document_ids = "never called"

    def retrieve(self, analysis, top_k=None, category=None, document_ids=None):
        self.document_ids = document_ids
        return []


def choices_and_bare_references() -> None:
    print("\n-- 5. a choice resends the question; a bare file name is an overview --")
    question = "اقرأ ملف PROJECT_SUMMARY المرفوع، وأجب منه فقط: ما آخر مرحلة؟"
    choices = SCOPE.resolve(question).choices(question)
    check([q for _, q in choices] == [
        "اقرأ ملف PROJECT_SUMMARY.md المرفوع، وأجب منه فقط: ما آخر مرحلة؟",
        "اقرأ ملف PROJECT_SUMMARY_v1_24.md المرفوع، وأجب منه فقط: ما آخر مرحلة؟",
    ], "each choice is the original question naming one file", str(choices))
    check(all(resolved(q) in ("lawsuit", "employment") for _, q in choices),
          "and each resolves to exactly its file")

    picked = "PROJECT_SUMMARY.md — رُفع 20/09/2026 — PROJECT SUMMARY — Unified-Stream Project"
    check(SCOPE.resolve(picked).is_bare_reference(picked), "a copied list line is a bare reference")
    check(SCOPE.resolve("PROJECT_SUMMARY.md").is_bare_reference("PROJECT_SUMMARY.md"), "so is a name alone")
    asked = "من ملف PROJECT_SUMMARY.md: ما آخر مرحلة؟"
    check(not SCOPE.resolve(asked).is_bare_reference(asked), "a real question is not")


def the_pipeline_uses_it() -> None:
    print("\n-- 4. the pipeline: one file searched alone, or a question back --")
    service = RagService.__new__(RagService)
    service.analyzer = QueryAnalyzer()
    service.document_scope = SCOPE
    service.retriever = _Retriever()
    service.llm = SimpleNamespace(model="fake")
    service.metrics = None
    # The empty-evidence path is not what this tests; stop right after retrieval.
    service._knowledge_only_answer = lambda *a, **k: ChatResponse(answer="", grounded=False)

    service.answer(ChatRequest(question="من ملف PROJECT_SUMMARY_v1_24 ما آخر مرحلة؟"))
    check(service.retriever.document_ids == ["employment"], "a single named file is searched alone",
          str(service.retriever.document_ids))

    service.retriever = _Retriever()
    reply = service.answer(ChatRequest(question="اقرأ ملف PROJECT_SUMMARY وأجب منه"))
    check(service.retriever.document_ids == "never called", "an ambiguous name searches nothing")
    check(reply.refusal_reason == "ambiguous-document" and not reply.grounded,
          "and replies with a question, not an answer", reply.answer[:80])
    check("employment" not in reply.answer and "PROJECT_SUMMARY_v1_24.md" in reply.answer,
          "naming files, not internal ids")

    service.retriever = _Retriever()
    service.answer(ChatRequest(question="ما السقف الأقصى لغرامة التأخير؟"))
    check(service.retriever.document_ids is None, "a question naming no file searches everything")

    service.retriever = _Retriever()
    service.answer(ChatRequest(question="اقرأ ملف PROJECT_SUMMARY", document_ids=["lawsuit"]))
    check(service.retriever.document_ids == ["lawsuit"], "a file chosen explicitly is never second-guessed")


def main() -> None:
    print("document scope")
    names_are_resolved()
    touching_a_word_is_not_naming()
    the_ambiguity_reply()
    choices_and_bare_references()
    the_pipeline_uses_it()
    print("\n" + "=" * 64)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("a named file is answered from that file, and a shared name is asked about")


if __name__ == "__main__":
    main()
