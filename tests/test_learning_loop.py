"""Learning from how people ask: rephrasings become synonyms, repeats come from memory.

Checks that a failed question followed by a successful rephrasing teaches exactly one
pair, only to the person who asked, and only when the two questions differ the way a
synonym does; that a person's own synonyms reach their search and nobody else's; and
that a remembered answer is never served once the evidence behind it has changed.

Offline: a temporary database, an in-memory keyword index over invented passages.

Run: python tests/test_learning_loop.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

WORKDIR = Path(tempfile.mkdtemp(prefix="rag-learning-loop-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(WORKDIR / 'test.db').as_posix()}"

from app.models.database import engine, init_database, session_scope  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.services.keyword_index import KeywordIndex  # noqa: E402
from app.services.learning_loop import AnswerMemory, RephraseLearner, answered  # noqa: E402
from app.services.query_analysis import QueryAnalyzer  # noqa: E402
from app.services.query_rewrite import SynonymLexicon, parse_term  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


class RecordingKnowledge:
    """Stands in for KnowledgeService.propose and keeps what it was asked to record."""

    def __init__(self):
        self.proposed = []

    def propose(self, db, user, proposal):
        self.proposed.append((user.id, proposal))


def reply(grounded: bool):
    return SimpleNamespace(grounded=grounded, answer_source="documents" if grounded else "none", choices=[],
                           answer="x", timings_ms={}, plan=None)


ALICE = SimpleNamespace(id="u-alice", email="alice@example.test", role="contributor")
BOB = SimpleNamespace(id="u-bob", email="bob@example.test", role="contributor")


def main() -> int:
    init_database()
    index = KeywordIndex()
    index.build_from([
        ("c1", "d1", "غرامة التأخير اليومية في العقد 1,200 درهم عن كل يوم"),
        ("c2", "d1", "مدة تنفيذ المشروع اثنا عشر شهرا من تاريخ استلام الموقع"),
    ])
    analyzer = QueryAnalyzer()

    print("\n=== 1. a person's own synonyms reach their search only ===")
    lexicon = SynonymLexicon()
    plain = lexicon.expand("كم الحسم اليومي")
    mine = lexicon.expand("كم الحسم اليومي", extra=[("الحسم", "غرامة")])
    check(not any("غرام" in t for t in plain.terms), "without personal terms: no expansion")
    check(any("غرام" in t for t in mine.terms), "with them: the file's word is searched too", str(mine.terms))
    check(analyzer.analyze("كم الحسم اليومي", [("الحسم", "غرامة")]).expansions != analyzer.analyze("كم الحسم اليومي").expansions,
          "the analyzer passes them through for that question only")

    print("\n=== 2. rephrasing teaches one pair ===")
    knowledge = RecordingKnowledge()
    learner = RephraseLearner(analyzer, index, knowledge)
    check(learner.pair("كم الحسم اليومي في العقد", "كم غرامة التأخير اليومية في العقد") is not None,
          "an unknown word swapped for known ones is a pair")
    check(learner.pair("كم الحسم والعقوبة اليومية", "كم غرامة التأخير اليومية") is None,
          "two unknown words: which one meant what is a guess — no pair")
    check(learner.pair("كم غرامة التأخير", "ما مدة تنفيذ المشروع") is None,
          "a different question altogether is not a rephrasing")

    learner.observe(ALICE, "كم الحسم اليومي في العقد", reply(False))
    learned = learner.observe(ALICE, "كم غرامة التأخير اليومية في العقد", reply(True))
    check(learned is not None and learned[0].startswith("حسم"), "observed live: the pair is learned", str(learned))
    check(len(knowledge.proposed) == 1 and knowledge.proposed[0][0] == ALICE.id, "recorded once, for Alice")
    proposal = knowledge.proposed[0][1] if knowledge.proposed else None
    check(proposal is not None and str(proposal.scope) == "user" and str(proposal.type) == "terminology",
          "as her own terminology — personal scope, so active without review")
    check(proposal is not None and parse_term(proposal.content) is not None,
          "worded so the lexicon reads it back", proposal.content if proposal else "")

    print("\n=== 3. what does not teach ===")
    count = len(knowledge.proposed)
    learner.observe(BOB, "كم الحسم اليومي في العقد", reply(False))
    learner.observe(ALICE, "كم غرامة التأخير اليومية في العقد", reply(True))
    check(len(knowledge.proposed) == count, "Bob's failure does not pair with Alice's success")
    slow = RephraseLearner(analyzer, index, knowledge, window=0.01)
    slow.observe(ALICE, "كم الحسم اليومي في العقد", reply(False))
    time.sleep(0.05)
    slow.observe(ALICE, "كم غرامة التأخير اليومية في العقد", reply(True))
    check(len(knowledge.proposed) == count, "a retry after the window is a new question, not a rephrasing")
    learner.observe(ALICE, "كم غرامة التأخير اليومية في العقد", reply(True))
    check(len(knowledge.proposed) == count, "two answered questions in a row teach nothing")
    check(learner.observe(None, "سؤال", reply(False)) is None, "no reader, nothing to learn")

    print("\n=== 4. repeated questions from memory ===")
    memory = AnswerMemory()
    stamp = memory.stamp("u-alice")
    first = reply(True)
    memory.put(ALICE, "كم غرامة التأخير؟", stamp, first)
    again = memory.get(ALICE, "كم   غرامة التأخير؟", stamp)
    check(again is not None, "the same question, spaced differently, comes from memory")
    check(again is not first, "a copy is served, never the stored object")
    check(memory.get(BOB, "كم غرامة التأخير؟", memory.stamp("u-bob")) is None, "another person's answer is never served")
    with session_scope() as session:
        session.add(Document(id=str(uuid.uuid4()), filename="new.pdf", stored_path="x", content_hash="h1",
                             owner_id="u-alice", status="completed", extra_metadata={}))
    changed = memory.stamp("u-alice")
    check(changed != stamp, "a new document changes the stamp")
    check(memory.get(ALICE, "كم غرامة التأخير؟", changed) is None, "and the remembered answer is no longer served")
    memory.put(ALICE, "سؤال بلا إجابة", changed, reply(False))
    check(memory.get(ALICE, "سؤال بلا إجابة", changed) is None, "a refusal is never remembered")
    check(answered(reply(True)) and not answered(reply(False)), "answered means grounded in documents")

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
