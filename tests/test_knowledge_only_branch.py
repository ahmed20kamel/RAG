"""Whether approved knowledge can answer alone — tested apart from the retriever.

The live experiment could not answer this. It asked questions no document covers and the
retriever returned eight chunks for every one of them, including a question about paint
colour in a building the corpus has never heard of. The branch that lets knowledge answer
alone is guarded by `not candidates` / `not context`, and neither condition occurs. So
the experiment measured the model refusing on irrelevant context, not the gate.

That leaves an open question the experiment cannot settle: if the branch were reached,
would it behave? Here the retriever is replaced with one that returns nothing, which is
the only way to put the branch under test. What is checked is the part that matters —
that `eligible` answers from an item and cites it, that `gated` refuses the same input,
that an item below the threshold is refused in both, and that two approved items
disagreeing with nothing to separate them are refused rather than picked between.

No network, no model, no database.

Run: python tests/test_knowledge_only_branch.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.knowledge import KnowledgeScope, KnowledgeType  # noqa: E402
from app.schemas.chat import ChatRequest  # noqa: E402
from app.services.knowledge_service import ActiveKnowledge  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.rag_service import RagService  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def item(content: str, source_text: str = "قرار الإدارة") -> ActiveKnowledge:
    """A real `ActiveKnowledge`, not a look-alike.

    The branch reads fields off this record through two other modules, and a hand-rolled
    stand-in drifts from it silently — a renamed field would leave this suite passing
    against a shape the production path no longer produces.
    """
    return ActiveKnowledge(
        item_id=str(uuid.uuid4()),
        version_id=str(uuid.uuid4()),
        type=KnowledgeType.FACT,
        scope=KnowledgeScope.GLOBAL,
        content=content,
        source_text=source_text,
        source_document_id=None,
        confidence=1.0,
        version_no=1,
        tags=[],
    )


def service(
    mode: str, items: list[ActiveKnowledge], scores: dict[str, float], answer: str
) -> RagService:
    """A RagService whose retriever finds nothing, so the branch is the only path out."""
    calls: list[tuple[str, str]] = []

    def chat(system: str, prompt: str) -> str:
        calls.append((system, prompt))
        return answer

    built = RagService(
        analyzer=QueryAnalyzer(),
        retriever=SimpleNamespace(retrieve=lambda *a, **k: []),
        context_builder=SimpleNamespace(build=lambda *a, **k: ("", [])),
        llm=SimpleNamespace(chat=chat, model="stub"),
        validator=SimpleNamespace(),
        knowledge=SimpleNamespace(),
        fact_sheet=SimpleNamespace(),
        contracts=SimpleNamespace(),
        planner=SimpleNamespace(),
        coverage=SimpleNamespace(),
        completion=SimpleNamespace(),
        knowledge_service=SimpleNamespace(active_for=lambda db, user: items),
        knowledge_answer_mode=mode,
        enable_knowledge_layer=True,
    )
    built.calls = calls  # type: ignore[attr-defined]
    built._semantic_scores = lambda analysis, user: scores  # type: ignore[assignment]
    built._record_knowledge_usage = lambda *a, **k: []  # type: ignore[assignment]
    built._store_trace = lambda *a, **k: None  # type: ignore[assignment]
    return built


USER = SimpleNamespace(id="u-1", team_id=None, email="someone@local")
QUESTION = "من يعتمد طلبات التمديد في مشروع أوريون؟"


def ask(rag: RagService):
    with patch("app.services.rag_service.session_scope"):
        return rag.answer(ChatRequest(question=QUESTION), user=USER)


def eligible_mode_answers_from_knowledge() -> None:
    print("\n-- 1. in `eligible`, an approved item above the bar answers --")
    taught = item("جهة الاعتماد لطلبات التمديد هي لجنة العمليات", source_text="محضر اللجنة")
    rag = service("eligible", [taught], {taught.item_id: 0.81}, "لجنة العمليات [محضر اللجنة].")
    response = ask(rag)

    check(response.grounded is True, "the answer is returned as grounded")
    check("لجنة العمليات" in response.answer, "and carries the taught value")
    check(response.sources == [], "with no document sources, because there were none")
    check(response.retrieved_chunks == 0, "and nothing retrieved")
    check(
        any("راجع المصدر" in w for w in (response.validation.warnings if response.validation else [])),
        "the reader is warned it rests on knowledge alone",
    )
    check(len(rag.calls) == 1, "one generation, not two")  # type: ignore[attr-defined]
    check(
        taught.content in rag.calls[0][1],  # type: ignore[attr-defined]
        "the item's text is what was sent to the model",
    )


def gated_mode_refuses_the_same_input() -> None:
    print("\n-- 2. the same input in `gated` refuses --")
    taught = item("جهة الاعتماد لطلبات التمديد هي لجنة العمليات")
    rag = service("gated", [taught], {taught.item_id: 0.81}, "لجنة العمليات.")
    response = ask(rag)

    check(response.grounded is False, "it refuses")
    check(response.answer != "لجنة العمليات.", "the taught value is not returned")
    check(len(rag.calls) == 0, "and the model was never called")  # type: ignore[attr-defined]


def below_the_threshold_refuses() -> None:
    print("\n-- 3. a weak match is refused even in `eligible` --")
    taught = item("جهة الاعتماد لطلبات التمديد هي لجنة العمليات")
    rag = service("eligible", [taught], {taught.item_id: 0.40}, "لجنة العمليات.")
    response = ask(rag)

    check(response.grounded is False, "0.40 does not clear the 0.62 bar")
    check(len(rag.calls) == 0, "no generation was attempted")  # type: ignore[attr-defined]


def unattributed_knowledge_refuses() -> None:
    print("\n-- 4. an item with no attribution cannot answer alone --")
    taught = item("جهة الاعتماد لطلبات التمديد هي لجنة العمليات", source_text="   ")
    rag = service("eligible", [taught], {taught.item_id: 0.90}, "لجنة العمليات.")
    response = ask(rag)

    check(response.grounded is False, "an answer with nothing to cite is refused")
    check(len(rag.calls) == 0, "no generation was attempted")  # type: ignore[attr-defined]


def an_unresolved_disagreement_refuses() -> None:
    print("\n-- 5. two approved items disagreeing is refused, not decided --")
    left = item("مدة صلاحية تصريح الدخول 90 يومًا من تاريخ الإصدار", source_text="دليل الأمن")
    right = item("مدة صلاحية تصريح الدخول 30 يومًا من تاريخ الإصدار", source_text="مذكرة لاحقة")
    rag = service(
        "eligible", [left, right], {left.item_id: 0.80, right.item_id: 0.79}, "90 يومًا."
    )
    response = ask(rag)

    check(response.grounded is False, "neither value is asserted")
    check(len(rag.calls) == 0, "the model was not asked to choose")  # type: ignore[attr-defined]


def a_disagreement_written_in_words_refuses() -> None:
    """Two approved items disagreeing only in words behave exactly as the digit case
    in section 5 does — refused, and the model never asked to choose. Value extraction
    reads Arabic number words, so the disagreement is visible.
    """
    print("\n-- 6. the same disagreement written in words is refused too --")
    left = item("مدة صلاحية تصريح الدخول تسعون يومًا", source_text="دليل الأمن")
    right = item("مدة صلاحية تصريح الدخول ثلاثون يومًا", source_text="مذكرة لاحقة")
    rag = service(
        "eligible", [left, right], {left.item_id: 0.80, right.item_id: 0.79}, "تسعون يومًا."
    )
    response = ask(rag)

    check(response.grounded is False, "neither value is asserted")
    check(len(rag.calls) == 0, "the model was not asked to choose")  # type: ignore[attr-defined]


if __name__ == "__main__":
    eligible_mode_answers_from_knowledge()
    gated_mode_refuses_the_same_input()
    below_the_threshold_refuses()
    unattributed_knowledge_refuses()
    an_unresolved_disagreement_refuses()
    a_disagreement_written_in_words_refuses()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("The knowledge-only branch behaves as specified when it is reached.")
