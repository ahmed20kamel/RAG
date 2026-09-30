"""Turns approved knowledge into something an answer may use — and nothing more.

This is the seam between taught knowledge and the document pipeline, and it is built so
the pipeline cannot tell the difference when there is nothing to add. With no approved
items the arm returns an empty result whose every field is falsy, the prompt is assembled
from exactly the same pieces as before, and the answer is byte-identical. That property
is what the regression gate checks, so it is stated here as the contract rather than
left as an accident of the current code.

Three separations are enforced:

* Factual knowledge is offered as evidence, marked as taught, never merged into the
  document citations.
* Rules and preferences are directives. They shape how an answer reads; they are never
  presented as something the answer can assert.
* A taught fact that disagrees with a document is not resolved here. Both are shown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.knowledge import KnowledgeScope, KnowledgeType
from app.core.text import content_terms, normalize, numeric_tokens, strip_thousands
from app.services.knowledge_service import ActiveKnowledge

logger = logging.getLogger(__name__)

#: How much of a question's wording an item must echo before it is offered at all. The
#: knowledge base is company-wide; without this every answer would carry every item.
RELEVANCE_FLOOR = 0.34
#: Behavioural items are few and general, so they apply without a wording test — but a
#: hard cap keeps a prompt from filling with directives.
MAX_DIRECTIVES = 6
MAX_FACTS = 8


@dataclass(slots=True)
class ScoredKnowledge:
    """One selected item and how it was found, kept for the usage record."""

    knowledge: ActiveKnowledge
    score: float = 0.0
    # semantic | lexical | directive — which path put it in front of the answer.
    retrieval: str = "lexical"


@dataclass(slots=True)
class KnowledgeConflict:
    """A taught value and a document value that cannot both be right."""

    knowledge: ActiveKnowledge
    document_value: str
    document_citation: int
    taught_value: str

    def describe(self) -> str:
        return (
            f"القيمة في المستند [{self.document_citation}]: {self.document_value} — "
            f"القيمة في المعرفة المعتمدة: {self.taught_value}"
        )


@dataclass(slots=True)
class KnowledgeContribution:
    """What the arm offers one answer. Empty means the pipeline runs unchanged."""

    facts: list[ActiveKnowledge] = field(default_factory=list)
    rules: list[ActiveKnowledge] = field(default_factory=list)
    preferences: list[ActiveKnowledge] = field(default_factory=list)
    conflicts: list[KnowledgeConflict] = field(default_factory=list)
    scores: dict[str, ScoredKnowledge] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.facts or self.rules or self.preferences)

    def all_items(self) -> list[ActiveKnowledge]:
        return [*self.facts, *self.rules, *self.preferences]


class KnowledgeArm:
    """Selects approved knowledge for a question and renders it for the prompt."""

    def select(
        self,
        question: str,
        available: list[ActiveKnowledge],
        semantic_scores: dict[str, float] | None = None,
    ) -> KnowledgeContribution:
        """Chooses what this question may draw on.

        Semantic hits come from the knowledge collection and carry a similarity score;
        lexical overlap is the fallback when the embedding host is unreachable, so a
        cold vector index degrades the selection rather than emptying it.
        """
        if not available:
            return KnowledgeContribution()
        scores = semantic_scores or {}

        terms = {t for t in content_terms(question) if len(t) > 2}
        contribution = KnowledgeContribution()

        for knowledge in available:
            if knowledge.type in (KnowledgeType.RULE, KnowledgeType.PREFERENCE):
                # A directive about how to answer does not have to mention the subject.
                target = (
                    contribution.rules
                    if knowledge.type is KnowledgeType.RULE
                    else contribution.preferences
                )
                target.append(knowledge)
                continue

            semantic = scores.get(knowledge.item_id, 0.0)
            lexical = self._overlap(terms, knowledge)
            if semantic > 0.0 or lexical >= RELEVANCE_FLOOR:
                knowledge_score = max(semantic, lexical)
                contribution.facts.append(knowledge)
                contribution.scores[knowledge.item_id] = ScoredKnowledge(
                    knowledge=knowledge,
                    score=round(knowledge_score, 4),
                    retrieval="semantic" if semantic > 0.0 else "lexical",
                )

        # Personal preferences win over team ones for the person who set them.
        contribution.preferences.sort(key=lambda k: k.scope is not KnowledgeScope.USER)
        # Ranked by how well the item matched, not by how confident its author felt.
        contribution.facts.sort(
            key=lambda k: (contribution.scores.get(k.item_id).score if k.item_id in contribution.scores else 0.0, k.confidence),
            reverse=True,
        )

        contribution.facts = contribution.facts[:MAX_FACTS]
        contribution.rules = contribution.rules[:MAX_DIRECTIVES]
        contribution.preferences = contribution.preferences[:MAX_DIRECTIVES]

        if not contribution.is_empty:
            logger.info(
                "Knowledge arm: %s fact(s), %s rule(s), %s preference(s)",
                len(contribution.facts), len(contribution.rules), len(contribution.preferences),
            )
        return contribution

    def find_conflicts(
        self, contribution: KnowledgeContribution, sources: list
    ) -> list[KnowledgeConflict]:
        """Taught numbers that a cited document states differently.

        Only numbers are compared, and only inside a passage the taught item already
        overlaps in wording. A looser test would report every coincidence as a dispute;
        the point is to surface a real disagreement, not to manufacture doubt.
        """
        conflicts: list[KnowledgeConflict] = []
        for knowledge in contribution.facts:
            taught = {strip_thousands(v) for v in numeric_tokens(knowledge.content)}
            if not taught:
                continue
            taught_terms = {t for t in content_terms(knowledge.content) if len(t) > 2}

            for source in sources:
                excerpt = getattr(source, "excerpt", "") or ""
                if not excerpt:
                    continue
                shared = taught_terms & {
                    t for t in content_terms(excerpt) if len(t) > 2
                }
                if len(shared) < 2:
                    continue

                stated = {strip_thousands(v) for v in numeric_tokens(excerpt)}
                if not stated or taught & stated:
                    continue  # they agree, or the passage carries no comparable number

                conflicts.append(
                    KnowledgeConflict(
                        knowledge=knowledge,
                        document_value=", ".join(sorted(stated)[:3]),
                        document_citation=getattr(source, "citation", 0),
                        taught_value=", ".join(sorted(taught)[:3]),
                    )
                )
                break

        if conflicts:
            logger.info("Knowledge arm: %s conflict(s) with document evidence", len(conflicts))
        contribution.conflicts = conflicts
        return conflicts

    # -- prompt rendering -------------------------------------------------
    @staticmethod
    def render_facts(contribution: KnowledgeContribution, conflicts: list | None = None) -> str:
        """Taught facts under their own heading, never mixed into the document block.

        The heading is not decoration. The model receives document passages and taught
        claims as two labelled sections in a fixed order, so "which of these is a source
        and which is a colleague's note" is answered by the structure of the prompt
        rather than by a sentence asking it to remember.
        """
        if not contribution.facts:
            return ""
        lines = [
            "=== المعرفة المعتمدة ===",
            "(أقرّها مراجع من الفريق — ليست مقتطفات من المستندات، ولا تُقدَّم عليها)",
        ]
        for knowledge in contribution.facts:
            source = f" — المصدر: {knowledge.source_text}" if knowledge.source_text else ""
            lines.append(f"- {knowledge.content}{source}")

        reported = conflicts if conflicts is not None else contribution.conflicts
        if reported:
            lines.append("")
            lines.append("=== تعارضات مرصودة ===")
            lines.append(
                "القيم التالية مختلفة بين مصدرين. اعرض كل قيمة ومصدرها، "
                "ولا ترجّح واحدة ما لم يُذكر الترجيح صراحةً أدناه:"
            )
            for conflict in reported:
                lines.append(f"- {conflict.describe()}")
        return "\n".join(lines)

    @staticmethod
    def render_directives(contribution: KnowledgeContribution) -> str:
        """Rules and preferences as their own labelled sections — never as evidence."""
        if not (contribution.rules or contribution.preferences):
            return ""
        lines: list[str] = []
        if contribution.rules:
            lines.append("=== قواعد معتمدة ===")
            lines.append("(تحكم طريقة بناء الإجابة، ولا يجوز اقتباسها كحقيقة)")
            lines.extend(f"- {r.content}" for r in contribution.rules)
        if contribution.preferences:
            if lines:
                lines.append("")
            lines.append("=== تفضيلات الأسلوب ===")
            lines.append("(تمسّ الصياغة وحدها، ولا تُسقط أي معلومة ولا تُغيّر أي قيمة)")
            lines.extend(f"- {p.content}" for p in contribution.preferences)
        return "\n".join(lines)

    @staticmethod
    def _overlap(question_terms: set[str], knowledge: ActiveKnowledge) -> float:
        if not question_terms:
            return 0.0
        haystack = f"{knowledge.content} {knowledge.source_text} {' '.join(knowledge.tags)}"
        described = {t for t in content_terms(haystack) if len(t) > 2}
        if not described:
            return 0.0
        # Share of the *question* the item covers: a long item should not win simply by
        # containing more words than a short one.
        return len(question_terms & described) / len(question_terms)

    @staticmethod
    def mentions(answer: str, knowledge: ActiveKnowledge) -> bool:
        """Whether the answer actually used this item, for the usage record."""
        normalised = normalize(answer)
        terms = {t for t in content_terms(knowledge.content) if len(t) > 2}
        if not terms:
            return False
        hits = sum(1 for t in terms if t in normalised)
        return hits / len(terms) >= 0.6
