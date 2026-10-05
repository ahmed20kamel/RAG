"""Every question is kept, and what was asked before shapes what is searched now.

Checks the question log end to end, without the model: a follow-up is searched in the
context of the question it follows and inside the files that answered it; a wording that
found nothing, followed by one that found the answer, is searched as the second next
time; a repeated question is answered from the database while nothing changed, and not
after a thumbs-down; and none of it crosses from one person to another.

Offline: a temporary database. Run: python tests/test_question_memory.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-qmem-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"

from app.models.database import engine, init_database  # noqa: E402
from app.schemas.chat import ChatResponse, SourceReference  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.question_memory import QuestionMemory  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


ALICE = SimpleNamespace(id="alice-1", email="alice@test")
BOB = SimpleNamespace(id="bob-1", email="bob@test")


def source(document_id: str) -> SourceReference:
    fields = SourceReference.model_fields
    values = {name: "" for name, f in fields.items() if f.annotation is str}
    values.update(document_id=document_id, filename=f"{document_id}.pdf")
    for name, f in fields.items():
        if f.is_required() and name not in values:
            values[name] = 0 if f.annotation in (int, float) else None
    return SourceReference.model_validate(values)


def answered(text: str, document_id: str, answer_id: str) -> ChatResponse:
    return ChatResponse(answer=text, grounded=True, sources=[source(document_id)], answer_id=answer_id)


def refused() -> ChatResponse:
    return ChatResponse(answer="لم أجد", grounded=False, answer_source="none")


def main() -> int:
    init_database()
    memory = QuestionMemory(QueryAnalyzer())

    print("=== 1. follow-ups ===")
    for q in ("وكم مدته؟", "طيب والغرامة؟", "ومتى ينتهي؟", "ما المبلغ؟"):
        check(memory.is_follow_up(q), f"«{q}» reads as a follow-up")
    for q in ("ما قيمة العقد؟", "ما شروط الدفع المتفق عليها؟", "هل فيه غرامة تأخير؟"):
        check(not memory.is_follow_up(q), f"«{q}» stands on its own")

    first = "ما قيمة العقد؟"
    memory.record(ALICE, first, "conv-a", None, answered("القيمة 125,000 [1]", "doc-contract", "ans-1"), "s1")
    follow = memory.prepare(ALICE, "وكم مدته؟", "conv-a", has_scope=False)
    check(follow.follow_up and "قيمة العقد" in follow.question and follow.subject == first,
          "the follow-up is searched with the question it follows", follow.question)
    check(memory.analyzer.analyze(follow.question).multi_part is False,
          "…as one question, not as two parts to answer separately", follow.question)
    check(follow.document_ids == ["doc-contract"], "…inside the file that answered it", str(follow.document_ids))
    memory.record(ALICE, "وكم مدته؟", "conv-a", follow, answered("12 شهرًا [1]", "doc-contract", "ans-2"), "s1")
    chain = memory.prepare(ALICE, "ومتى ينتهي؟", "conv-a", has_scope=False)
    check(chain.subject == first and chain.question.count("بخصوص") == 1,
          "a chain of follow-ups keeps the original subject, not the last fragment", chain.question)
    scoped = memory.prepare(ALICE, "وكم مدته؟", "conv-a", has_scope=True)
    check(scoped.document_ids is None, "a question that chose its own files keeps them")
    other_conv = memory.prepare(ALICE, "وكم مدته؟", "conv-b", has_scope=False)
    check(not other_conv.follow_up, "another conversation has no previous turn to follow")
    check(not memory.prepare(BOB, "وكم مدته؟", "conv-a", has_scope=False).follow_up,
          "someone else's conversation is never read, even under the same id")

    print("\n=== 2. a wording that worked ===")
    failed = "ما قيمة الضمان البنكي؟"
    memory.record(ALICE, failed, "conv-c", None, refused(), "s1")
    worked = "ما قيمة خطاب الضمان؟"
    memory.record(ALICE, worked, "conv-c", None, answered("50,000 [1]", "doc-guarantee", "ans-3"), "s1")
    again = memory.prepare(ALICE, failed, "conv-d", has_scope=False)
    check(again.question == worked, "asked again in the old wording: searched in the one that worked", again.question)
    check(memory.prepare(BOB, failed, "conv-x", has_scope=False).question == failed,
          "another person's wording is not learned from")
    memory.record(ALICE, "ما رقم الرخصة التجارية؟", "conv-e", None, refused(), "s1")
    memory.record(ALICE, "ما مدة التنفيذ؟", "conv-e", None, answered("12 شهرًا", "doc-contract", "ans-4"), "s1")
    unrelated = memory.prepare(ALICE, "ما رقم الرخصة التجارية؟", "conv-f", has_scope=False)
    check(unrelated.question == "ما رقم الرخصة التجارية؟",
          "an unrelated question that came next is not taken as the rewording", unrelated.question)
    memory.record(ALICE, "ما تاريخ الاستلام؟", "conv-g", None, refused(), "s1")
    memory.record(ALICE, "ما تاريخ الاستلام في ملف محضر.pdf؟", "conv-g", None,
                  answered("1/3/2025", "doc-handover", "ans-5"), "s1")
    clicked = memory.prepare(ALICE, "ما تاريخ الاستلام؟", "conv-h", has_scope=False)
    check(clicked.question == "ما تاريخ الاستلام في ملف محضر.pdf؟",
          "a suggestion clicked after a refusal is learned as where to look", clicked.question)

    print("\n=== 3. repeats, kept across restarts ===")
    check(memory.recall(ALICE, first, "s1") is not None, "the same question, nothing changed: answered from the log")
    check(memory.recall(ALICE, first, "s2") is None, "a document changed: answered afresh")
    check(memory.recall(BOB, first, "s1") is None, "another person's answer is never given")
    check(memory.recall(ALICE, "وكم مدته؟", "s1") is None, "a follow-up's answer is not reused outside its conversation")
    restarted = QuestionMemory(QueryAnalyzer())
    check(restarted.recall(ALICE, first, "s1") is not None, "a fresh instance — a restart — still remembers")

    print("\n=== 4. thumbs-down ===")
    forgotten = memory.feedback(ALICE, "ans-1", "down")
    check(first in forgotten, "the thumbs-down names the question to drop from fast memory", str(forgotten))
    check(memory.recall(ALICE, first, "s1") is None, "a thumbs-down answer is not repeated")
    check(memory.feedback(BOB, "ans-3", "down") == [], "no one can mark someone else's answer")
    memory.feedback(ALICE, "ans-3", "down")
    check(memory.prepare(ALICE, failed, "conv-z", has_scope=False).question == failed,
          "a wording whose answer was marked wrong is not searched any more")

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
