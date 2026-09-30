"""Builds the evidence set and decides what each requirement must be checked against.

The planner never looks for "the answer". It answers a narrower question: which stated
values could satisfy this requirement? Binding is by value kind and shared wording, both
of which are already computed during ingestion, so this adds no model call and no extra
scan of the knowledge base.
"""

from __future__ import annotations

import logging

from app.core.contract import AnswerContract, Requirement, RequirementKind
from app.core.evidence import EvidenceItem, EvidenceSet
from app.core.text import content_terms, parse_date
from app.schemas.chat import SourceFact, SourceReference

logger = logging.getLogger(__name__)

# Which stated value kinds can satisfy which requirement. "attribute" appears widely
# because a labelled table row is how these documents state most facts.
COMPATIBLE: dict[RequirementKind, tuple[str, ...]] = {
    RequirementKind.PERSON: ("person", "attribute"),
    RequirementKind.ORGANIZATION: ("organization", "attribute"),
    RequirementKind.NUMBER: ("amount", "percentage", "identifier", "duration", "attribute"),
    RequirementKind.DATE: ("date", "attribute"),
    RequirementKind.EVENT: ("date", "attribute"),
    RequirementKind.ATTRIBUTE: ("attribute", "identifier", "person", "organization"),
}

# How much of a requirement's wording an item must echo before it counts as a candidate
# for it. Primary evidence clears a lower bar because it was ranked as directly relevant.
BIND_THRESHOLD = 0.34
BIND_THRESHOLD_PRIMARY = 0.2
MAX_SINGLE_CANDIDATES = 3
MAX_ENUMERATION_CANDIDATES = 12
MIN_NAME_WORDS = 2
# Confidence given to a candidate that matched on kind alone, with no shared wording.
FALLBACK_CONFIDENCE = 0.15

# Requirements whose kind names the shape of the value owed. For these, "no candidate"
# is a finding, not a pass: Arabic derivation routinely defeats lexical overlap
# ("وقّع" / "الموقّع" / "التوقيع" share no stem), and without a fallback the check would
# declare a requirement satisfied purely because it had nothing to compare against.
# EVENT and ATTRIBUTE are excluded: their values are open-ended, so kind alone says
# nothing about whether an item is a plausible answer.
DETERMINATE = (
    RequirementKind.PERSON,
    RequirementKind.ORGANIZATION,
    RequirementKind.DATE,
    RequirementKind.NUMBER,
)


class EvidencePlanner:
    def build_evidence(
        self, sources: list[SourceReference], facts: list[SourceFact], context: str
    ) -> EvidenceSet:
        """Fold the retrieved citations and extracted facts into one addressable set."""
        evidence = EvidenceSet(context=context)
        heading_of = {s.citation: s.heading or s.section for s in sources}
        document_of = {s.citation: s.document_id for s in sources}
        section_of = {s.citation: s.section_id for s in sources}

        for fact in facts:
            evidence.add(
                EvidenceItem(
                    kind=fact.kind or "attribute",
                    value=fact.value,
                    label=fact.label,
                    citation=fact.citation,
                    document_id=document_of.get(fact.citation, ""),
                    section_id=section_of.get(fact.citation, ""),
                    section_heading=fact.section_heading or heading_of.get(fact.citation, ""),
                    primary=fact.primary,
                )
            )

        logger.info("Evidence set: %s item(s) from %s fact(s)", len(evidence), len(facts))
        return evidence

    def bind(
        self, contract: AnswerContract, evidence: EvidenceSet
    ) -> dict[str, list[EvidenceItem]]:
        """Candidate values for each requirement, best first."""
        bound: dict[str, list[EvidenceItem]] = {}
        for requirement in contract.requirements:
            candidates = self._candidates(requirement, evidence, contract.chronological)
            limit = (
                MAX_ENUMERATION_CANDIDATES if requirement.enumeration else MAX_SINGLE_CANDIDATES
            )
            bound[requirement.key] = candidates[:limit]
        logger.info(
            "Bound evidence: %s", {k: len(v) for k, v in bound.items()}
        )
        return bound

    def _candidates(
        self, requirement: Requirement, evidence: EvidenceSet, chronological: bool
    ) -> list[EvidenceItem]:
        compatible = COMPATIBLE.get(requirement.kind, ("attribute",))
        scored: list[tuple[float, EvidenceItem]] = []
        # For a chronological contract the dated items are the answer set: they were
        # already selected as the events in scope, so asking them to echo the question's
        # wording as well would discard the very entries the answer must cover.
        dated_are_required = chronological and requirement.kind is RequirementKind.DATE

        for item in evidence.of_kinds(compatible):
            if not self._shape_fits(requirement.kind, item):
                continue
            if dated_are_required and item.kind == "date":
                item.confidence = 1.0
                scored.append((1.0, item))
                continue
            overlap = self._overlap(requirement.terms, item)
            floor = BIND_THRESHOLD_PRIMARY if item.primary else BIND_THRESHOLD
            if overlap < floor:
                continue
            item.confidence = round(overlap + (0.1 if item.primary else 0.0), 3)
            scored.append((item.confidence, item))

        if not scored and requirement.kind in DETERMINATE:
            return self._fallback(requirement, evidence, compatible)

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored]

    def _fallback(
        self, requirement: Requirement, evidence: EvidenceSet, compatible: tuple[str, ...]
    ) -> list[EvidenceItem]:
        """Primary values of the right shape, when the wording matched none of them.

        Arabic derivation routinely defeats lexical overlap — "وقّع", "الموقّع" and
        "التوقيع" share no stem — so a requirement can find no anchor and then be
        declared satisfied purely because it had nothing to compare against. These
        candidates exist to stop that, but they are marked ``shape_only``: which of them
        the question meant is unknown, so the requirement asks only that the answer state
        *some* value of this kind. Demanding one particular candidate would turn a
        correct answer into a wrong one.
        """
        candidates = [
            item for item in evidence.of_kinds(compatible)
            if item.primary and self._shape_fits(requirement.kind, item)
        ]
        if not candidates:
            return []
        for item in candidates:
            item.confidence = FALLBACK_CONFIDENCE
        requirement.shape_only = True
        logger.info(
            "Requirement %s has no lexical anchor — checked by shape over %s candidate(s)",
            requirement.key, len(candidates),
        )
        return candidates[:MAX_SINGLE_CANDIDATES]

    @staticmethod
    def _overlap(terms: set[str], item: EvidenceItem) -> float:
        """Share of the requirement's words the item's label or section echoes."""
        if not terms:
            return 0.0
        described = content_terms(item.label) | content_terms(item.section_heading)
        return len(terms & described) / len(terms)

    @staticmethod
    def _shape_fits(kind: RequirementKind, item: EvidenceItem) -> bool:
        """Reject values that cannot possibly answer this kind of requirement."""
        value = item.value.strip()
        if not value:
            return False
        if kind is RequirementKind.DATE:
            return parse_date(value) is not None or parse_date(item.label) is not None
        if kind is RequirementKind.NUMBER:
            return any(character.isdigit() for character in value)
        if kind in (RequirementKind.PERSON, RequirementKind.ORGANIZATION):
            words = [t for t in content_terms(value) if not t.isdigit()]
            return len(words) >= MIN_NAME_WORDS
        return True
