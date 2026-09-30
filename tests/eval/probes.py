"""Harder retrieval probes, kept beside the evaluation set and never mixed into it.

Empty until written for your corpus. Two kinds:

**Paraphrases** reword a question from `dataset.py` the way people actually ask —
colloquial, with synonyms the files do not use, or in the other language — and inherit
its gold values and sections by id. A set that every configuration passes cannot say
whether a change helped; rewordings usually can.

**Order probes** name a value in force and the value it replaced, for documents that
correct themselves by appending. A probe passes when the first passage stating the value
in force outranks the first stating the superseded one. Choose values that occur only in
the version they belong to, or the order says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Paraphrase:
    qid: str
    #: The id of the question in `dataset.py` whose gold values and sections this inherits.
    of: str
    question: str


@dataclass(frozen=True, slots=True)
class OrderProbe:
    qid: str
    question: str
    wanted: tuple[str, ...]
    superseded: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompoundProbe:
    """Two questions from `dataset.py` asked as one; every part's gold must be answered."""

    qid: str
    of: tuple[str, ...]
    question: str


COMPOUND: list[CompoundProbe] = [
    # CompoundProbe("C-1", ("F1", "D1"), "What is the contract number, and when was it signed?"),
]

PARAPHRASES: list[Paraphrase] = [
    # Paraphrase("P-F1", "F1", "How long do I have to notify a claim?"),
]

ORDER_PROBES: list[OrderProbe] = [
    # OrderProbe("S1", "ما قيمة الكشف المعتمد؟", wanted=("210,000",), superseded=("185,000",)),
]
