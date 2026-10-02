"""Post-answer validation — deterministic, no second model call.

Checks that every value in the answer exists in the context that was actually sent,
that important evidence was not ignored, and that multi-part questions were answered
in full. Findings are reported rather than silently corrected.
"""

from __future__ import annotations

import logging
import re

from app.core.retrieval import EvidenceTier
from app.core.text import (
    content_terms,
    extract_dates,
    normalize,
    numeric_tokens,
    parse_date,
    strip_thousands,
)
from app.schemas.chat import AnswerValidation, SourceFact, SourceReference
from app.services.query_analysis import Intent, QueryAnalysis

logger = logging.getLogger(__name__)

CITATION = re.compile(r"\[(\d{1,2})\]")
MIN_VALUE_LENGTH = 3
PART_COVERAGE_THRESHOLD = 0.34

# Kinds whose value names a person or organisation — the ones an exhaustive answer
# must list individually rather than summarise.
NAMED_KINDS = frozenset({"attribute", "person", "organization"})


class AnswerValidator:
    def missing_facts(
        self, analysis: QueryAnalysis, answer: str, facts: list[SourceFact]
    ) -> list[SourceFact]:
        """Facts the evidence supports for this question but the answer never states.

        Only applied to questions that must enumerate — for a focused question, leaving
        out an unrelated fact is correct behaviour, not an omission.
        """
        if not facts or not (analysis.exhaustive or analysis.role_specific):
            return []

        normalised_answer = normalize(answer)
        query_terms = content_terms(analysis.question)
        missing: list[SourceFact] = []

        # A timeline answer is judged by its dates: every dated entry the evidence
        # supplies must appear, otherwise "آخر تحديثات" silently drops events.
        if analysis.intent is Intent.TIMELINE:
            answered = set(extract_dates(answer))
            # Only entries whose label is itself a date — a bare date entity carries the
            # literal label "date" and would otherwise be reported missing forever.
            return [
                fact for fact in facts
                if fact.kind == "date" and parse_date(fact.label) and fact.label not in answered
            ]

        for fact in facts:
            if fact.kind not in NAMED_KINDS or not self._names_someone(fact.value):
                continue
            # A fact is owed to the answer when the question's own words point at it,
            # through the fact's label or the heading of the section it came from. That
            # keeps "من هم أطراف القضية؟" bound to the parties section instead of
            # demanding every name anywhere in the primary evidence.
            targeted = bool(
                query_terms & (content_terms(fact.label) | content_terms(fact.section_heading))
            )
            if not (targeted and fact.primary):
                continue
            if not self._value_present(fact.value, normalised_answer):
                missing.append(fact)
        return missing

    @staticmethod
    def _names_someone(value: str) -> bool:
        """Only values that actually name a party — not bare numbers or dates."""
        terms = [t for t in content_terms(value) if not t.isdigit()]
        return len(terms) >= 2

    @staticmethod
    def _value_present(value: str, normalised_answer: str) -> bool:
        """A name counts as present when its distinctive words appear in the answer."""
        normalised_value = normalize(value)
        if normalised_value and normalised_value in normalised_answer:
            return True
        terms = [t for t in content_terms(value) if len(t) > 2]
        if not terms:
            return False
        answer_terms = content_terms(normalised_answer)
        hits = sum(1 for t in terms if t in answer_terms)
        return hits >= max(1, min(2, len(terms)))

    def validate(
        self,
        analysis: QueryAnalysis,
        answer: str,
        context: str,
        sources: list[SourceReference],
        facts: list[SourceFact] | None = None,
        omitted: list[SourceFact] | None = None,
    ) -> AnswerValidation:
        """`omitted` lets the caller reuse a result it already computed, so the answer
        that gets validated and the one the retry is built from can never disagree."""
        cited = sorted({int(n) for n in CITATION.findall(answer)})
        primary = [s.citation for s in sources if s.tier == EvidenceTier.PRIMARY]
        uncited = [c for c in primary if c not in cited]

        unsupported = self._unsupported_values(answer, context, facts or [])
        unanswered = self._unanswered_parts(analysis, answer)
        conflicts = self._conflicts(sources)
        if omitted is None:
            omitted = self.missing_facts(analysis, answer, facts or [])

        warnings: list[str] = []
        if omitted:
            warnings.append(
                "حقائق مدعومة بالمصادر لم تَرِد في الإجابة: "
                + "، ".join(f"{f.label}: {f.value}"[:70] for f in omitted[:6])
            )
        if not cited and sources:
            warnings.append("الإجابة لا تحتوي على أي إشارة مرقّمة إلى المصادر.")
        if unsupported:
            warnings.append(
                "قيم وردت في الإجابة ولم يتم العثور عليها في المصادر المرسلة: "
                + "، ".join(unsupported[:6])
            )
        if uncited:
            warnings.append(
                "أدلة أساسية استُرجعت ولم تُستخدم في الإجابة: "
                + "، ".join(f"[{c}]" for c in uncited)
            )
        if unanswered:
            warnings.append("أجزاء من السؤال قد لا تكون مُجابة: " + " | ".join(unanswered))
        if conflicts:
            warnings.append("قيم متعارضة بين المصادر: " + " | ".join(conflicts[:4]))

        complete = (
            not unsupported and not unanswered and not omitted and bool(cited or not sources)
        )

        if warnings:
            logger.info("Answer validation flagged %s issue(s)", len(warnings))

        return AnswerValidation(
            complete=complete,
            cited_sources=cited,
            uncited_evidence=uncited,
            unsupported_values=unsupported,
            unanswered_parts=unanswered,
            omitted_facts=[f"{f.label}: {f.value}" for f in omitted],
            conflicts=conflicts,
            warnings=warnings,
        )

    @staticmethod
    def _unsupported_values(
        answer: str, context: str, facts: list[SourceFact]
    ) -> list[str]:
        """Any number, date or identifier in the answer must occur in the evidence.

        The evidence is the excerpts *and* the fact sheet. Both are verbatim source
        text — the fact sheet's labels and values are extracted at ingestion, never
        generated — so a date that reached the model only through a dated section
        heading is supported, while anything the model makes up is still in neither.
        """
        evidence = context + " " + " ".join(f"{f.label} {f.value}" for f in facts)
        haystack = strip_thousands(normalize(evidence))
        unsupported: list[str] = []
        for value in numeric_tokens(answer):
            cleaned = strip_thousands(value).strip(".,%")
            if len(cleaned) < MIN_VALUE_LENGTH or not any(ch.isdigit() for ch in cleaned):
                continue
            if cleaned not in haystack:
                unsupported.append(value)
        return sorted(set(unsupported))

    @staticmethod
    def _unanswered_parts(analysis: QueryAnalysis, answer: str) -> list[str]:
        if not analysis.multi_part or len(analysis.parts) < 2:
            return []
        answer_terms = content_terms(answer)
        unanswered: list[str] = []
        for part in analysis.parts:
            terms = {t for t in content_terms(part) if len(t) > 3}
            if not terms:
                continue
            coverage = len(terms & answer_terms) / len(terms)
            if coverage < PART_COVERAGE_THRESHOLD:
                unanswered.append(part[:70])
        return unanswered

    @staticmethod
    def _conflicts(sources: list[SourceReference]) -> list[str]:
        """Same section cited at two versions is the conflict that matters here."""
        by_section: dict[str, set[str]] = {}
        for source in sources:
            key = f"{source.filename}::{source.section_id}"
            if source.version:
                by_section.setdefault(key, set()).add(source.version)
        return [
            f"{key.split('::')[0]} بإصدارات مختلفة: {', '.join(sorted(versions))}"
            for key, versions in by_section.items()
            if len(versions) > 1
        ]
