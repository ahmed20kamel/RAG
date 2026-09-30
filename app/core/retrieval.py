"""Shared retrieval types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class EvidenceTier(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    RELATED = "related"


@dataclass(slots=True)
class Candidate:
    """One retrieved chunk plus every score and flag gathered along the way."""

    chunk_id: str
    document_id: str
    filename: str
    document_title: str
    section: str
    section_id: str
    heading: str
    parent_section: str
    content: str
    version: str = ""
    category: str = ""
    language: str = ""
    has_table: bool = False

    # Provenance only. Carried from the parser to the citation and read by nothing in
    # between: no filter, score, fusion or ordering may consult these, or the format a
    # document happens to be in would start influencing what gets retrieved.
    locator: str = ""
    page: int | None = None

    vector_score: float | None = None
    keyword_score: float | None = None
    vector_rank: int | None = None
    keyword_rank: int | None = None
    fused_score: float = 0.0
    rerank_score: float = 0.0
    # Position of this chunk's section in the document's dated sections, newest first.
    timeline_rank: int | None = None

    # -- temporal and class signals, and the record of what they did ------------
    #
    # Ranking adjustments have to be explainable, so each one is kept as the number that
    # was added rather than folded silently into `rerank_score`. A boost nobody can
    # account for is indistinguishable from a bug, and the first question asked of any
    # surprising ordering is "why did that come first".
    #
    # The date this passage is about, when it states one. Read from the heading first
    # and the body second, and never used unless the question asked about time.
    section_date: tuple[int, int, int] | None = None
    #: Whether this passage is the document's own bookkeeping — a changelog, a file
    #: inventory, an index — rather than its subject.
    is_metadata: bool = False
    #: Whether this passage says it is the version in force, says it has been retired,
    #: or says neither. Read from its own words, not from a date.
    standing: str = ""
    #: Signed contributions actually applied, for the trace. Zero when a signal did not
    #: apply, which is the normal case for most questions.
    recency_boost: float = 0.0
    metadata_adjustment: float = 0.0
    standing_adjustment: float = 0.0
    #: The cross-encoder's reading of how well this passage answers the question, in
    #: [0, 1], when it was scored. None when the model is off or the passage ranked
    #: below the scored head — which is not the same as scoring zero.
    cross_score: float | None = None
    cross_adjustment: float = 0.0
    #: One short phrase per adjustment, naming the cue that caused it.
    rank_notes: list[str] = field(default_factory=list)

    @property
    def date_label(self) -> str:
        if self.section_date is None:
            return ""
        year, month, day = self.section_date
        return f"{day:02d}/{month:02d}/{year}"

    origins: set[str] = field(default_factory=set)
    tier: EvidenceTier = EvidenceTier.SUPPORTING

    @property
    def origin_label(self) -> str:
        return "+".join(sorted(self.origins)) or "unknown"
