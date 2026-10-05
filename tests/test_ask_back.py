"""When no answer is found, the system asks back instead of stopping — and keeps the
question for whoever improves it.

A refusal used to be a dead end: "not enough information". Now, when the search landed
on files that did not answer, those files come back as choices, each re-asking the same
question inside one file; when it found nothing, the reader's recent files do. The
wording of an unanswered question is kept so the misses can be read and fixed; an
answered question still keeps only its hash.

Offline: the refusal path is called directly, with stand-ins for retrieved sources.

Run: python tests/test_ask_back.py
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

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-ask-back-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"

from app.models.database import engine, init_database, session_scope  # noqa: E402
from app.models.metrics import RequestMetric  # noqa: E402
from app.schemas.chat import QueryPlan  # noqa: E402
from app.services.metrics import MetricsRecorder  # noqa: E402
from app.services.rag_service import INSUFFICIENT_ANSWER, RagService  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


def service() -> RagService:
    rag = object.__new__(RagService)
    rag.web_search = SimpleNamespace(enabled=False)
    rag.llm = SimpleNamespace(model="test-model")
    return rag


def source(filename: str, section: str):
    return SimpleNamespace(filename=filename, section=section, heading=section)


def main() -> int:
    init_database()
    rag = service()
    analysis = SimpleNamespace(language="ar", question="كم الحسم اليومي؟")

    print("\n=== 1. found files that did not answer: offered back ===")
    sources = [source("عقد التوريد.pdf", "البند 9 → الغرامات"), source("عقد التوريد.pdf", "البند 2"),
               source("محضر الاجتماع.pdf", "الدفعات"), source("خطاب 44.pdf", ""), source("آخر.pdf", "س")]
    choices = rag._suggest_from_sources(analysis.question, sources)
    check(len(choices) == 3, "at most three, one per file", str([c.label for c in choices]))
    check(choices[0].label == "الغرامات — عقد التوريد.pdf", "labelled with the section and the file", choices[0].label)
    check(choices[0].question == "كم الحسم اليومي في ملف عقد التوريد.pdf؟", "each re-asks the same question in that file", choices[0].question)
    check(choices[2].label == "خطاب 44.pdf", "a file without a section is offered by name")

    response = rag._empty(QueryPlan(), {}, analysis, "model-read-evidence-and-refused", retrieved=5,
                          looked_at=["الغرامات"], suggestions=choices)
    check(response.choices == choices and "اختر" in response.answer, "the refusal becomes a question back")
    check(response.answer != INSUFFICIENT_ANSWER and not response.grounded, "still not presented as an answer")

    print("\n=== 2. without suggestions the refusal is unchanged ===")
    plain = rag._empty(QueryPlan(), {}, analysis, "insufficient-internal-evidence")
    check(plain.answer == INSUFFICIENT_ANSWER and plain.choices == [], "same text, no choices")

    print("\n=== 3. English questions are asked back in English ===")
    english = rag._empty(QueryPlan(), {}, SimpleNamespace(language="en", question="x"), "r", suggestions=choices)
    check(english.answer.startswith("I could not find"), english.answer[:40])

    print("\n=== 4. only unanswered questions keep their wording ===")
    recorder = MetricsRecorder()
    answered = SimpleNamespace(answer_source="documents", grounded=True, refusal_reason="", timings_ms={},
                               validation=None, retrieved_chunks=3, model="m", answer_id="a1")
    recorder.record("chat", "سؤال أُجيب عنه", answered, 100, None)
    recorder.record("chat", "سؤال لم يُجب عنه", plain, 100, None)
    with session_scope() as session:
        rows = {r.outcome: r.question for r in session.query(RequestMetric).all()}
    check(rows.get("answered") == "", "an answered question keeps only its hash")
    check(rows.get("refused") == "سؤال لم يُجب عنه", "an unanswered one keeps its wording, to be read and fixed")

    print("\n=== 5. a short question several files match: which file? ===")
    from app.services.query_analysis import QueryAnalyzer

    analyzer = QueryAnalyzer()

    def hits(*files):
        return [SimpleNamespace(filename=f) for f in files]

    vague = analyzer.analyze("ما المبلغ؟")
    asked = rag._which_file(vague, hits("عقد.pdf", "فاتورة.pdf", "عقد.pdf"), QueryPlan(), {})
    check(asked is not None and len(asked.choices) == 2, "two files match «ما المبلغ؟»: asked which one")
    check(asked is not None and all(" في ملف " in c.question for c in asked.choices),
          "each choice re-asks the question inside one file")
    check(asked is not None and asked.refusal_reason == "ambiguous-question" and not asked.grounded,
          "recorded as a question back, not as an answer")
    check(rag._which_file(vague, hits("عقد.pdf", "عقد.pdf"), QueryPlan(), {}) is None,
          "one file matches: answered, not asked back")
    specific = analyzer.analyze("ما مبلغ الدفعة المقدمة في العقد؟")
    check(rag._which_file(specific, hits("عقد.pdf", "فاتورة.pdf"), QueryPlan(), {}) is None,
          "a specific question is searched, however many files match")

    print("\n=== 6. one person cannot fill the queue ===")
    from contextlib import ExitStack

    from app.api.routes.chat import MAX_QUESTIONS_PER_PERSON, admit, waiting_on
    from app.exceptions import TooManyQuestionsError

    def refused(who) -> bool:
        try:
            admit(who)
            return False
        except TooManyQuestionsError as exc:
            return exc.status_code == 429

    person = SimpleNamespace(id="busy-reader")
    with ExitStack() as open_pages:
        first = ExitStack()
        first.enter_context(waiting_on(person))
        for _ in range(MAX_QUESTIONS_PER_PERSON - 1):
            open_pages.enter_context(waiting_on(person))
        check(refused(person), "a third question while two pages wait is refused (429)")
        check(not refused(SimpleNamespace(id="someone-else")), "someone else is not held back by it")
        first.close()
        check(not refused(person), "a page closed — answered or abandoned — frees its place at once")

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
