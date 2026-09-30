"""The evaluation set: questions about your own documents, with what a right answer holds.

Empty on purpose. A question set is specific to a corpus, and one written for somebody
else's documents measures nothing about yours. Write 30–40 questions before relying on
the quality gate's retrieval and answer stages, and keep them honest:

* `gold` holds values that MUST appear in a complete answer, copied from the source file
  exactly as it writes them — "28", "HSE-F-022", "1:1.5".
* `sections` holds fragments of the section headings the answer should cite.
* `document` is the file name the evidence lives in.
* `in_kb=False` marks a question the documents cannot answer. Include several: refusing
  correctly is measured as carefully as answering correctly.

Cover every kind of question people ask — single facts, figures, dates, people and
organisations, several values at once, comparisons, timelines — and phrase them the way
people actually do, not the way the headings are worded.

Once written, treat this file as frozen. A set whose questions change alongside the code
it scores can be made to say anything.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvalQuestion:
    qid: str
    category: str
    question: str
    gold: tuple[str, ...] = ()
    document: str | None = None
    sections: tuple[str, ...] = ()
    in_kb: bool = True
    notes: str = ""


QUESTIONS: list[EvalQuestion] = [
    # EvalQuestion("F1", "factual", "ما مهلة إخطار المطالبة؟",
    #              ("28",), "Claims_Procedure.md", ("مهلة الإخطار",)),
    # EvalQuestion("O1", "out_of_kb", "ما سعر صرف الدرهم مقابل اليورو اليوم؟", in_kb=False),
]

#: Accepted alternative spellings of a gold value — a name the documents write in Latin
#: letters and the model may transliterate, for example.
EQUIVALENTS: dict[str, tuple[str, ...]] = {}
