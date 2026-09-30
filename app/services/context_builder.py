"""Builds a tiered, numbered context block and the citations that mirror it.

Evidence is grouped so the model can tell what directly answers the question from what
merely supports it, and every block carries the provenance needed to cite it.
"""

from __future__ import annotations

import logging

from app.core.retrieval import Candidate, EvidenceTier
from app.schemas.chat import SourceReference

logger = logging.getLogger(__name__)

EXCERPT_CHARS = 320

TIER_HEADINGS: dict[EvidenceTier, str] = {
    EvidenceTier.PRIMARY: "[الأدلة الأساسية]",
    EvidenceTier.SUPPORTING: "[أدلة مساندة]",
    EvidenceTier.RELATED: "[أقسام ذات صلة]",
}


class ContextBuilder:
    def __init__(
        self,
        max_context_chars: int,
        primary_count: int = 3,
        narrow_context_chars: int | None = None,
        max_related_blocks: int = 3,
    ) -> None:
        self.max_context_chars = max_context_chars
        self.narrow_context_chars = narrow_context_chars or max_context_chars
        self.primary_count = primary_count
        self.max_related_blocks = max_related_blocks

    def build(
        self, candidates: list[Candidate], wide: bool = True
    ) -> tuple[str, list[SourceReference]]:
        budget = self.max_context_chars if wide else self.narrow_context_chars
        usable = [c for c in candidates if c.content.strip()]
        if not usable:
            return "", []

        self._assign_tiers(usable)
        usable = self._cap_related(usable)

        blocks: list[str] = []
        sources: list[SourceReference] = []
        used = 0
        position = 0
        current_tier: EvidenceTier | None = None

        for candidate in usable:
            body = candidate.content.strip()
            position += 1

            # The locator is appended, never substituted. A Markdown chunk has none, so
            # its header is assembled by exactly the expression it always was — which is
            # what keeps the indexed corpus citing itself identically.
            header = (
                f"[{position}] الملف: {candidate.filename}"
                f" | القسم: {candidate.section or candidate.heading or '-'}"
                f" | الإصدار: {candidate.version or '-'}"
            )
            if candidate.locator:
                header += f" | الموضع: {candidate.locator}"
            block = f"{header}\n{body}"
            tier_prefix = ""
            if candidate.tier is not current_tier:
                tier_prefix = f"{TIER_HEADINGS[candidate.tier]}\n"
                current_tier = candidate.tier

            if used + len(block) + len(tier_prefix) > budget and blocks:
                break

            blocks.append(tier_prefix + block)
            used += len(block) + len(tier_prefix)
            sources.append(self._to_source(candidate, position))

        logger.info(
            "Context built: %s blocks, %s chars (primary=%s)",
            len(blocks), used,
            sum(1 for s in sources if s.tier == EvidenceTier.PRIMARY),
        )
        return "\n\n---\n\n".join(blocks), sources

    def _cap_related(self, candidates: list[Candidate]) -> list[Candidate]:
        """Expansion evidence is the least dense per character, so bound how much enters."""
        kept: list[Candidate] = []
        related = 0
        for candidate in candidates:
            if candidate.tier is EvidenceTier.RELATED:
                if related >= self.max_related_blocks:
                    continue
                related += 1
            kept.append(candidate)
        return kept

    def _assign_tiers(self, candidates: list[Candidate]) -> None:
        direct = [
            c for c in candidates
            if c.origins
            & {"vector", "keyword", "entity", "same-section", "semantic-reserved", "timeline"}
        ]
        promoted = {c.chunk_id for c in direct[: self.primary_count]}

        for candidate in candidates:
            if candidate.chunk_id in promoted:
                candidate.tier = EvidenceTier.PRIMARY
            elif candidate.origins & {"vector", "keyword", "entity"}:
                candidate.tier = EvidenceTier.SUPPORTING
            else:
                candidate.tier = EvidenceTier.RELATED

        # Keep the retriever's order inside each tier. Re-sorting by rerank score here
        # would undo the slots it deliberately reserved — a dated section reached only
        # by the timeline sweep scores low and would be dropped by the budget again.
        position = {c.chunk_id: i for i, c in enumerate(candidates)}
        order = {EvidenceTier.PRIMARY: 0, EvidenceTier.SUPPORTING: 1, EvidenceTier.RELATED: 2}
        candidates.sort(key=lambda c: (order[c.tier], position[c.chunk_id]))

    @staticmethod
    def _to_source(candidate: Candidate, position: int) -> SourceReference:
        content = candidate.content.strip()
        return SourceReference(
            citation=position,
            document_id=candidate.document_id,
            filename=candidate.filename,
            title=candidate.document_title,
            section=candidate.section or candidate.heading,
            section_path=candidate.section,
            section_id=candidate.section_id,
            heading=candidate.heading,
            parent_section=candidate.parent_section,
            chunk_id=candidate.chunk_id,
            score=round(candidate.rerank_score, 4),
            vector_score=round(candidate.vector_score, 4) if candidate.vector_score else None,
            keyword_score=round(candidate.keyword_score, 4) if candidate.keyword_score else None,
            version=candidate.version,
            category=candidate.category,
            tier=candidate.tier,
            locator=candidate.locator,
            page=candidate.page,
            origin=candidate.origin_label,
            excerpt=content[:EXCERPT_CHARS] + ("…" if len(content) > EXCERPT_CHARS else ""),
        )
