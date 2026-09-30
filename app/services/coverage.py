"""Checks the answer against the contract, requirement by requirement.

Matching is chosen by the requirement's kind: dates are compared as dates so a date
written in words still counts, numbers ignore thousands separators, and names count as
stated when their distinctive words appear. A value is never called missing merely
because the answer spelled it differently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.contract import AnswerContract, Requirement, RequirementKind
from app.core.evidence import EvidenceItem
from app.core.text import (
    all_dates,
    content_terms,
    normalize,
    parse_date,
    strip_thousands,
)

logger = logging.getLogger(__name__)

NAME_HIT_RATIO = 0.6
MIN_NUMBER_LENGTH = 2

# How a stated value is checked when it is judged on its own rather than against a
# requirement — the same matching, chosen from the value's own kind.
EVIDENCE_KIND: dict[str, RequirementKind] = {
    "date": RequirementKind.DATE,
    "amount": RequirementKind.NUMBER,
    "percentage": RequirementKind.NUMBER,
    "duration": RequirementKind.NUMBER,
    "identifier": RequirementKind.NUMBER,
    "person": RequirementKind.PERSON,
    "organization": RequirementKind.ORGANIZATION,
}


@dataclass(slots=True)
class RequirementCoverage:
    requirement: Requirement
    required: list[EvidenceItem] = field(default_factory=list)
    covered: list[EvidenceItem] = field(default_factory=list)
    missing: list[EvidenceItem] = field(default_factory=list)
    # For a shape-only requirement: the answer states a value of the required kind,
    # even though it is not one of the candidates offered here.
    shape_met: bool = False

    @property
    def satisfied(self) -> bool:
        if not self.required:
            return True
        if self.requirement.shape_only:
            return self.shape_met or bool(self.covered)
        if self.requirement.enumeration:
            return not self.missing
        return bool(self.covered)


@dataclass(slots=True)
class CoverageResult:
    per_requirement: list[RequirementCoverage] = field(default_factory=list)

    @property
    def missing(self) -> list[EvidenceItem]:
        seen: set[tuple[str, str, str]] = set()
        out: list[EvidenceItem] = []
        for entry in self.per_requirement:
            if entry.satisfied:
                continue
            for item in entry.missing or entry.required[:1]:
                if item.dedupe_key in seen:
                    continue
                seen.add(item.dedupe_key)
                out.append(item)
        return out

    @property
    def unmet(self) -> list[RequirementCoverage]:
        return [entry for entry in self.per_requirement if not entry.satisfied]

    @property
    def complete(self) -> bool:
        return not self.unmet

    @property
    def score(self) -> float:
        checked = [e for e in self.per_requirement if e.required]
        if not checked:
            return 1.0
        return round(sum(1 for e in checked if e.satisfied) / len(checked), 3)

    def counts(self) -> tuple[int, int]:
        required = sum(len(e.required) for e in self.per_requirement)
        covered = sum(len(e.covered) for e in self.per_requirement)
        return required, covered


class CoverageValidator:
    def check(
        self,
        contract: AnswerContract,
        bound: dict[str, list[EvidenceItem]],
        answer: str,
    ) -> CoverageResult:
        normalised = normalize(answer)
        answer_dates = all_dates(answer)
        answer_terms = content_terms(answer)
        bare = strip_thousands(normalised)

        result = CoverageResult()
        for requirement in contract.requirements:
            candidates = bound.get(requirement.key, [])
            entry = RequirementCoverage(requirement=requirement, required=list(candidates))
            for item in candidates:
                if self._states(requirement.kind, item, normalised, bare, answer_dates, answer_terms):
                    entry.covered.append(item)
                else:
                    entry.missing.append(item)
            if requirement.shape_only:
                entry.shape_met = self._shape_met(requirement.kind, answer_dates, answer_terms)
            result.per_requirement.append(entry)

        if result.unmet:
            logger.info(
                "Coverage %.2f — unmet: %s",
                result.score, [e.requirement.key for e in result.unmet],
            )
        return result

    def stated_count(self, evidence, answer: str) -> int:
        """How many of the retrieved values this answer states, over the whole set.

        Used to compare two candidate answers. The contract only sees the values bound to
        a requirement, so it cannot notice that a rewrite dropped something it never
        asked about — this can.
        """
        normalised = normalize(answer)
        answer_dates = all_dates(answer)
        answer_terms = content_terms(answer)
        bare = strip_thousands(normalised)
        return sum(
            1 for item in evidence.items
            if self._states(
                EVIDENCE_KIND.get(item.kind, RequirementKind.ATTRIBUTE),
                item, normalised, bare, answer_dates, answer_terms,
            )
        )

    @staticmethod
    def _shape_met(kind: RequirementKind, answer_dates: set, answer_terms: set[str]) -> bool:
        """Does the answer state any value of this kind at all?

        Only kinds whose shape is recognisable without a model are decided here. A person
        or an organisation is not, so those fall back to the candidates themselves: an
        answer naming none of them has not answered the ask.
        """
        if kind is RequirementKind.DATE:
            return bool(answer_dates)
        if kind is RequirementKind.NUMBER:
            return any(term.isdigit() for term in answer_terms)
        return False

    def _states(
        self,
        kind: RequirementKind,
        item: EvidenceItem,
        normalised_answer: str,
        bare_answer: str,
        answer_dates: set,
        answer_terms: set[str],
    ) -> bool:
        if kind is RequirementKind.DATE:
            return self._date_stated(item, answer_dates)
        if kind is RequirementKind.NUMBER:
            return self._number_stated(item, bare_answer)
        if kind in (RequirementKind.PERSON, RequirementKind.ORGANIZATION):
            return self._name_stated(item.value, normalised_answer, answer_terms)
        return self._value_stated(item.value, normalised_answer, answer_terms)

    @staticmethod
    def _date_stated(item: EvidenceItem, answer_dates: set) -> bool:
        when = parse_date(item.label) or parse_date(item.value)
        return when is not None and when in answer_dates

    @staticmethod
    def _number_stated(item: EvidenceItem, bare_answer: str) -> bool:
        """Any number the item carries, compared without thousands separators."""
        numbers = [
            strip_thousands(token)
            for token in normalize(item.value).replace("،", " ").split()
            if any(ch.isdigit() for ch in token)
        ]
        meaningful = [n.strip(".,%:") for n in numbers if len(n.strip(".,%:")) >= MIN_NUMBER_LENGTH]
        if not meaningful:
            return True
        return any(number in bare_answer for number in meaningful)

    @staticmethod
    def _name_stated(value: str, normalised_answer: str, answer_terms: set[str]) -> bool:
        """A name is stated when its distinctive words are in the answer.

        Any number inside the name has to appear: "الطرف 1" and "الطرف 2" share every
        word but the digit, and a ratio over words alone would read either as the other.
        """
        if normalize(value) in normalised_answer:
            return True
        tokens = content_terms(value)
        digits = {t for t in tokens if t.isdigit()}
        if digits and not digits <= answer_terms:
            return False
        terms = [t for t in tokens if len(t) > 2 and not t.isdigit()]
        if not terms:
            return bool(digits) and digits <= answer_terms
        hits = sum(1 for t in terms if t in answer_terms)
        return hits / len(terms) >= NAME_HIT_RATIO

    @staticmethod
    def _value_stated(value: str, normalised_answer: str, answer_terms: set[str]) -> bool:
        if normalize(value) in normalised_answer:
            return True
        terms = [t for t in content_terms(value) if len(t) > 2]
        if not terms:
            return False
        return sum(1 for t in terms if t in answer_terms) / len(terms) >= NAME_HIT_RATIO
