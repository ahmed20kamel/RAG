"""Reranking.

`Reranker` is the extension point: a local cross-encoder can be dropped in later by
implementing this interface and swapping it in the container — nothing else changes.
The default implementation scores features that are already available, so it costs
no model call, which matters on hardware where generation alone takes over a minute.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.core.retrieval import Candidate
from app.core.text import all_dates, content_terms, normalize
from app.services.evidence_class import classify_section
from app.services.query_analysis import Intent, QueryAnalysis
from app.services.supersession import read_status
from app.services.temporal import age_weight, evidence_date, recency_weight

logger = logging.getLogger(__name__)

W_FUSED = 1.0
W_VECTOR = 1.0
W_TERM_COVERAGE = 0.9
W_IDENTIFIER = 1.2
W_HEADING = 0.6
W_STRUCTURE = 0.25
W_PHRASE = 0.5
#: What a synonym of a query term counts for in coverage, against 1.0 for the word itself.
SYNONYM_CREDIT = 0.5

#: Applied only when the question asked about time. Sized against the lexical features
#: deliberately: below `W_TERM_COVERAGE`, so a recent passage about something else can
#: never overtake an older one that actually answers the question, and above
#: `W_HEADING`, so among passages that are all plausible the newest is preferred. The
#: measured failure was not that the wrong subject won — it was that the right subject's
#: newest section never beat its own older ones.
W_RECENCY = 0.8
#: A passage stating a date the question named itself. Stronger than the generic
#: preference because it is a direct match on something the reader asked for, not an
#: inference about what they probably meant.
W_EXPLICIT_DATE = 1.1
#: Removed from a bookkeeping section when the question is about the subject. A
#: subtraction rather than a filter: a changelog that is genuinely the best answer can
#: still win, it just stops winning on the strength of sharing the words "file" and
#: "update" with the question.
W_METADATA_PENALTY = 0.9
#: Added when the question really is about the document as an object.
W_METADATA_BONUS = 0.4

#: Removed from a passage that a competing passage has superseded.
#:
#: Only ever subtracted, never added. The first version of this gave a passage a bonus
#: for declaring itself in force, and the measurement showed why that is wrong: asked
#: about delay penalties, a passage about the expert's scope ranked first purely for
#: carrying the words "النسخة النافذة". A statement about which version counts says
#: nothing about whether the subject is the one asked about.
#:
#: So nothing is promoted. A superseded passage is demoted, and only when a rival
#: passage about the same subject says it is the one in force — which is the only
#: situation in which being superseded means anything at all.
W_STANDING = 1.0

#: What it takes for one passage to supersede another. Three conditions, all measured,
#: and all three added after the first version of this rule demoted half a document.
#:
#: A section of standing reminders shares five content words with almost everything else
#: in the same file — they are all about the same matter. Counting shared words alone
#: therefore let one grab-bag section mark most of its document as superseded, and a
#: question about excavation inspection forms came back with no evidence at all.
#:
#: So the overlap must also be a large *share* of the smaller passage, and a passage
#: that would supersede more than a couple of rivals is treated as a grab-bag and
#: ignored: a correction replaces one or two versions of a fact, not ten.
RIVAL_MIN_SHARED_TERMS = 6
RIVAL_MIN_OVERLAP = 0.45
RIVAL_MAX_REPLACED = 3

TABLE_FRIENDLY_INTENTS = frozenset({Intent.NUMERIC, Intent.DATE, Intent.COMPARISON, Intent.TIMELINE})


def _covers_same_ground(one: set[str], other: set[str]) -> bool:
    """Whether two passages are close enough to be rival statements of one fact.

    Proportional, not a raw count. An absolute threshold treats a long section that
    touches on everything as a rival of every short section it brushes past, which is
    how one page of standing reminders came to supersede most of its own document.
    """
    if not one or not other:
        return False
    shared = len(one & other)
    if shared < RIVAL_MIN_SHARED_TERMS:
        return False
    return shared / min(len(one), len(other)) >= RIVAL_MIN_OVERLAP


class Reranker(ABC):
    """Reorders candidates for a given query."""

    name: str = "base"

    @abstractmethod
    def rerank(
        self, analysis: QueryAnalysis, candidates: list[Candidate], limit: int
    ) -> list[Candidate]:
        """Return candidates sorted best-first, truncated to `limit`."""


class FeatureReranker(Reranker):
    """Blends retrieval scores with lexical, structural and identifier evidence."""

    name = "feature"

    def rerank(
        self, analysis: QueryAnalysis, candidates: list[Candidate], limit: int
    ) -> list[Candidate]:
        if not candidates:
            return []

        # The standard form of the question, so a colloquial "كام" or "بندفع" does not
        # sit in the denominator as a word no passage can match.
        query_terms = content_terms(analysis.search_text or analysis.question)
        identifiers = [normalize(i) for i in analysis.identifiers]
        heading_targets = query_terms
        alternatives = analysis.term_alternatives

        def matched(terms: set[str]) -> float:
            """Query terms a passage covers; a synonym counts for half of the word asked.

            Half, as in the keyword search. At full credit a synonym of a word that is
            everywhere in a file reshuffled passages the question's own words already
            reached — measured on a legal file, it swapped the third and fourth sources of
            a question that merely said "في هذه القضية". A synonym should help a passage
            the question's own words miss, not reorder the ones they reach.
            """
            return sum(
                1.0 if t in terms
                else SYNONYM_CREDIT if (alternatives.get(t) and alternatives[t] & terms)
                else 0.0
                for t in query_terms
            )

        # Both new signals are prepared once for the whole pool rather than per
        # candidate. Recency is relative — a passage is "recent" compared with the
        # newest thing retrieved alongside it, not compared with today — so the frame of
        # reference has to be the pool, and a corpus whose latest entry is months old
        # still ranks its own newest material first instead of flattening to zero.
        self._prepare_signals(analysis, candidates)
        newest, oldest = self._span(candidates)

        for candidate in candidates:
            text = normalize(f"{candidate.section} {candidate.heading} {candidate.content}")
            chunk_terms = content_terms(candidate.content) | content_terms(candidate.section)

            coverage = matched(chunk_terms) / len(query_terms) if query_terms else 0.0
            identifier_match = (
                1.0 if identifiers and any(ident in text for ident in identifiers) else 0.0
            )
            heading_terms = content_terms(candidate.section)
            heading_match = (
                matched(heading_terms) / len(heading_targets) if heading_targets else 0.0
            )
            structure = (
                1.0 if candidate.has_table and analysis.intent in TABLE_FRIENDLY_INTENTS else 0.0
            )
            phrase = 1.0 if analysis.normalized and analysis.normalized in text else 0.0

            temporal = self._temporal_term(analysis, candidate, newest, oldest)
            metadata = self._metadata_term(analysis, candidate)
            standing = self._standing_term(candidate)

            # Rank fusion throws away how similar the chunk actually was. Keeping the
            # absolute similarity matters when the answer sits in the other language:
            # its lexical features are all zero, so rank alone buries it under
            # keyword noise from same-language chunks that merely share common words.
            candidate.rerank_score = (
                W_FUSED * candidate.fused_score
                + W_VECTOR * (candidate.vector_score or 0.0)
                + W_TERM_COVERAGE * coverage
                + W_IDENTIFIER * identifier_match
                + W_HEADING * heading_match
                + W_STRUCTURE * structure
                + W_PHRASE * phrase
                + temporal
                + metadata
                + standing
            )

        ranked = sorted(candidates, key=lambda c: c.rerank_score, reverse=True)
        logger.debug(
            "Reranked %s candidates, best=%.3f", len(ranked), ranked[0].rerank_score
        )
        return ranked[:limit]

    # -- temporal and class signals ---------------------------------------
    @staticmethod
    def _prepare_signals(analysis: QueryAnalysis, candidates: list[Candidate]) -> None:
        """Dates and section class, computed once per candidate.

        Only when the question needs them. Parsing dates out of every chunk of every
        query would add work to the overwhelming majority of questions that have no
        temporal component at all, and the result would be read by nothing.
        """
        needs_dates = analysis.temporal.is_temporal or bool(analysis.temporal.explicit_dates)
        for candidate in candidates:
            candidate.rank_notes = []
            candidate.recency_boost = 0.0
            candidate.metadata_adjustment = 0.0

            if needs_dates and candidate.section_date is None:
                candidate.section_date = evidence_date(
                    candidate.section, candidate.heading, candidate.content
                )
            classification = classify_section(candidate.section, candidate.heading)
            candidate.is_metadata = classification.is_metadata

            # Read once per candidate, and on every question. Which version of a fact is
            # in force is not a temporal question — it is a property the passage states
            # about itself — so gating this on temporal wording would leave the case it
            # was built for unprotected.
            candidate.standing = read_status(
                candidate.section, candidate.heading, candidate.content
            ).state

        # Supersession is a relation, not a property. A passage is only superseded by
        # another passage that covers the same subject and says it is the one in force;
        # read on its own, "المأمورية النافذة = 3 بنود" tells you nothing about a
        # passage on delay penalties, and must not rank one above the other.
        in_force = [c for c in candidates if c.standing == "current"]
        if in_force:
            terms = {
                c.chunk_id: content_terms(c.content) | content_terms(c.section)
                for c in candidates
            }
            for authority in in_force:
                replaced = [
                    c for c in candidates
                    if c.standing != "current"
                    and c.document_id == authority.document_id
                    and _covers_same_ground(terms[c.chunk_id], terms[authority.chunk_id])
                ]
                # A correction that replaces this much is not a correction.
                if len(replaced) > RIVAL_MAX_REPLACED:
                    continue
                for candidate in replaced:
                    candidate.standing = "superseded"


    @staticmethod
    def _span(
        candidates: list[Candidate],
    ) -> tuple[tuple[int, int, int] | None, tuple[int, int, int] | None]:
        dates = [c.section_date for c in candidates if c.section_date is not None]
        if not dates:
            return None, None
        return max(dates), min(dates)

    @staticmethod
    def _temporal_term(
        analysis: QueryAnalysis,
        candidate: Candidate,
        newest: tuple[int, int, int] | None,
        oldest: tuple[int, int, int] | None,
    ) -> float:
        """Newer-is-better credit, and only where the question asked for it.

        Three separate cases, in the order the question settles them:

        * a question naming a date rewards passages stating *that* date, and applies no
          generic preference at all — someone who names a date has said what they want
          more precisely than a cue list can guess it;
        * a question asking for the latest rewards proximity to the newest evidence
          retrieved alongside it;
        * a question asking what came first rewards the opposite.

        A passage carrying no date earns nothing here and loses nothing. It is ranked on
        its other features, which is the right outcome for material that simply does not
        say when it applies.
        """
        intent = analysis.temporal

        if intent.explicit_dates:
            if candidate.section_date is None and not candidate.content:
                return 0.0
            stated = all_dates(f"{candidate.section} {candidate.heading} {candidate.content}")
            hit = stated & set(intent.explicit_dates)
            if not hit:
                return 0.0
            year, month, day = sorted(hit)[-1]
            candidate.recency_boost = W_EXPLICIT_DATE
            candidate.rank_notes.append(
                f"+{W_EXPLICIT_DATE:.2f} يذكر التاريخ المطلوب {day:02d}/{month:02d}/{year}"
            )
            return W_EXPLICIT_DATE

        if intent.window is not None:
            # A relative period is a named date range, so it earns what a named date
            # earns — and nothing when the passage falls outside it, however new it is.
            stated = all_dates(f"{candidate.section} {candidate.heading} {candidate.content}")
            if candidate.section_date is not None:
                stated.add(candidate.section_date)
            inside = sorted(d for d in stated if intent.window.contains(d))
            if not inside:
                return 0.0
            year, month, day = inside[-1]
            candidate.recency_boost = W_EXPLICIT_DATE
            candidate.rank_notes.append(
                f"+{W_EXPLICIT_DATE:.2f} يقع داخل {intent.window.describe()} "
                f"({day:02d}/{month:02d}/{year})"
            )
            return W_EXPLICIT_DATE

        if intent.wants_latest:
            weight = recency_weight(candidate.section_date, newest)
            if weight <= 0.0:
                return 0.0
            boost = W_RECENCY * weight
            candidate.recency_boost = boost
            candidate.rank_notes.append(
                f"+{boost:.2f} أحدث ({candidate.date_label}) — {intent.describe()}"
            )
            return boost

        if intent.wants_earliest:
            weight = age_weight(candidate.section_date, oldest)
            if weight <= 0.0:
                return 0.0
            boost = W_RECENCY * weight
            candidate.recency_boost = boost
            candidate.rank_notes.append(
                f"+{boost:.2f} أقدم ({candidate.date_label}) — {intent.describe()}"
            )
            return boost

        return 0.0

    @staticmethod
    def _metadata_term(analysis: QueryAnalysis, candidate: Candidate) -> float:
        """Re-weighting between a document's bookkeeping and its subject.

        Never a filter. A changelog that genuinely answers the question can still win;
        what it can no longer do is win because its title shares the words "file" and
        "update" with a question about something else entirely.
        """
        if not candidate.is_metadata:
            return 0.0

        if analysis.metadata_intent.is_metadata:
            candidate.metadata_adjustment = W_METADATA_BONUS
            candidate.rank_notes.append(
                f"+{W_METADATA_BONUS:.2f} السؤال عن الملف نفسه "
                f"(«{analysis.metadata_intent.cue}»)"
            )
            return W_METADATA_BONUS

        candidate.metadata_adjustment = -W_METADATA_PENALTY
        candidate.rank_notes.append(
            f"-{W_METADATA_PENALTY:.2f} قسم إداري عن الملف، والسؤال عن الموضوع"
        )
        return -W_METADATA_PENALTY

    @staticmethod
    def _standing_term(candidate: Candidate) -> float:
        """Demote a version that another passage has replaced.

        Nothing is promoted here. The passage in force does not need a boost — it needs
        the versions it replaced to stop outranking it, which they did purely because
        their headings happened to match the question's wording more closely.
        """
        if candidate.standing != "superseded":
            return 0.0
        candidate.standing_adjustment = -W_STANDING
        candidate.rank_notes.append(f"-{W_STANDING:.2f} نسخة تجاوزها تصحيح لاحق")
        return -W_STANDING


class CrossEncoderReranker(FeatureReranker):
    """The feature ranking, with a cross-encoder's judgement added on top of its head.

    Two stages, the standard cascade: every candidate is scored on features, the best
    `top_n` are read by the model, and its score is added to theirs. Candidates below the
    head keep their feature score and cannot overtake a head that only gained, so the
    model reorders exactly the passages that compete for the final slots and costs
    nothing for the rest.

    Additive, never a replacement. The model does not know which date a question asked
    for, which version of a clause is in force, or that a changelog is bookkeeping; the
    feature terms do, and each of them was measured into place. The weight is sized so
    the model decides among plausible passages without overturning a named date.
    """

    name = "cross"
    NOTE = "نموذج إعادة الترتيب"

    def __init__(self, scorer, weight: float = 2.0, top_n: int = 12, budget_ms: int = 15000,
                 cache=None) -> None:
        from app.services.cross_encoder import PairCache

        self.scorer = scorer
        self.weight = weight
        self.top_n = top_n
        self.budget_ms = budget_ms
        self.cache = cache or PairCache()

    @staticmethod
    def passage_text(candidate: Candidate) -> str:
        # The section path carries what a clause is about when its body does not say:
        # "الغرامات → السقف" above a line that reads only "10% من قيمة العقد".
        return f"{candidate.section}\n{candidate.content}"

    def rerank(
        self, analysis: QueryAnalysis, candidates: list[Candidate], limit: int
    ) -> list[Candidate]:
        ranked = super().rerank(analysis, candidates, limit=len(candidates))
        if not ranked:
            return []

        head = ranked[: self.top_n]
        question = normalize(analysis.question)
        missing = [c for c in head if self.cache.get((question, c.chunk_id)) is None]
        if missing:
            scores = self.scorer.score(
                analysis.question, [self.passage_text(c) for c in missing], self.budget_ms
            )
            if scores is None:
                return ranked[:limit]
            for candidate, score in zip(missing, scores):
                self.cache.put((question, candidate.chunk_id), score)

        for candidate in head:
            score = self.cache.get((question, candidate.chunk_id))
            if score is None:  # evicted between the two loops; keep features only
                return ranked[:limit]
            bonus = self.weight * score
            candidate.cross_score = score
            candidate.cross_adjustment = bonus
            candidate.rerank_score += bonus
            candidate.rank_notes = [n for n in candidate.rank_notes if self.NOTE not in n]
            candidate.rank_notes.append(f"+{bonus:.2f} {self.NOTE} ({score:.2f})")

        head.sort(key=lambda c: c.rerank_score, reverse=True)
        return (head + ranked[self.top_n:])[:limit]
