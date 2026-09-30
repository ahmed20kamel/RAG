"""What an answer owes, stated explicitly.

A question is turned into a set of requirements before anything is generated, so the
system can afterwards say which ones the answer met. Requirements are derived from the
grammar of the question — its interrogatives and its parts — never from its wording, so
the same rules hold for any question over any document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RequirementKind(StrEnum):
    """The sort of value that satisfies a requirement.

    Each kind implies how an answer is checked against it: dates compare as dates,
    numbers as numbers, names as names.
    """

    PERSON = "person"
    ORGANIZATION = "organization"
    NUMBER = "number"
    DATE = "date"
    EVENT = "event"
    ATTRIBUTE = "attribute"


@dataclass(slots=True)
class Requirement:
    """One thing the answer has to state."""

    key: str
    kind: RequirementKind
    part: str
    terms: set[str] = field(default_factory=set)
    # An enumeration must cover every supported candidate; otherwise one is enough.
    enumeration: bool = False
    # Set when no evidence echoed the requirement's wording and the candidates were
    # chosen by kind alone. Such a requirement asks only that the answer state *a* value
    # of this kind — naming one particular candidate would be a guess, not a finding.
    shape_only: bool = False

    def describe(self) -> str:
        scope = "كل العناصر" if self.enumeration else "عنصر واحد على الأقل"
        return f"{self.part} → {self.kind} ({scope})"


@dataclass(slots=True)
class AnswerContract:
    """The full set of requirements for one question."""

    question: str
    requirements: list[Requirement] = field(default_factory=list)
    multi_part: bool = False
    chronological: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.requirements

    def by_key(self, key: str) -> Requirement | None:
        return next((r for r in self.requirements if r.key == key), None)
