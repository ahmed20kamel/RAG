"""Hybrid retrieval: vector + keyword, fused, entity-guided, then expanded.

Vector search alone was the source of the incomplete answers: it ranks by meaning, so
a question naming a contract number or spanning several sections lost the exact chunk
that held the fact. This combines three independent ways of finding a chunk and then
widens deliberately when the question needs more than one section.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from app.core.retrieval import Candidate
from app.core.text import content_terms, normalize
from app.models.knowledge import ChunkRecord
from app.services.embeddings import OllamaEmbeddingClient
from app.services.keyword_index import KeywordIndex
from app.services.knowledge_store import KnowledgeStore
from app.services.query_analysis import Intent, QueryAnalysis
from app.services.reranking import Reranker
from app.services.vector_store import QdrantVectorStore

logger = logging.getLogger(__name__)

RRF_K = 60


class _EmbeddingCache:
    """A question asked twice must not pay for embedding twice."""

    def __init__(self, max_entries: int = 256) -> None:
        self._data: OrderedDict[str, list[float]] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: str, compute) -> list[float]:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
        vector = compute()
        with self._lock:
            self._data[key] = vector
            self._data.move_to_end(key)
            if len(self._data) > self._max:
                self._data.popitem(last=False)
            self.misses += 1
        return vector


class HybridRetriever:
    def __init__(
        self,
        embedder: OllamaEmbeddingClient,
        store: QdrantVectorStore,
        keyword_index: KeywordIndex,
        knowledge: KnowledgeStore,
        reranker: Reranker,
        top_k: int,
        candidate_pool: int,
        score_threshold: float,
        vector_score_floor: float,
        min_rerank_score: float,
        max_expanded_candidates: int,
        wide_top_k: int,
        min_covered_terms: int = 1,
        timeline_sections: int = 14,
        reserved_semantic_slots: int = 3,
        recent_slots: int = 3,
        enable_keyword_search: bool = True,
        enable_entity_retrieval: bool = True,
        enable_expansion: bool = True,
    ) -> None:
        self.enable_keyword_search = enable_keyword_search
        self.enable_entity_retrieval = enable_entity_retrieval
        self.enable_expansion = enable_expansion
        self.embedder = embedder
        self.store = store
        self.keyword_index = keyword_index
        self.knowledge = knowledge
        self.reranker = reranker
        self.top_k = top_k
        self.candidate_pool = candidate_pool
        self.score_threshold = score_threshold
        self.vector_score_floor = vector_score_floor
        self.min_rerank_score = min_rerank_score
        self.max_expanded_candidates = max_expanded_candidates
        self.wide_top_k = wide_top_k
        self.min_covered_terms = min_covered_terms
        self.timeline_sections = timeline_sections
        self.reserved_semantic_slots = reserved_semantic_slots
        # Far fewer than the timeline reservation, and for a different question:
        # the newest section plus the context immediately around it, not a listing.
        self.recent_slots = recent_slots
        self.cache = _EmbeddingCache()

    def retrieve(
        self,
        analysis: QueryAnalysis,
        top_k: int | None = None,
        category: str | None = None,
        document_ids: list[str] | None = None,
    ) -> list[Candidate]:
        limit = top_k or (self.wide_top_k if analysis.wants_wide_retrieval else self.top_k)

        candidates: dict[str, Candidate] = {}
        self._add_vector_hits(analysis, candidates, category, document_ids)
        if self.enable_keyword_search:
            self._add_keyword_hits(analysis, candidates, document_ids)
        if self.enable_entity_retrieval:
            self._add_entity_hits(analysis, candidates, document_ids)
        # Widened from `Intent.TIMELINE` alone. The dated sections state their date in
        # their heading and nothing else, so neither the lexical nor the semantic arm
        # can reach them — this sweep is their only path into the pool. Gating it on the
        # timeline intent meant that "آخر تحديثات … بالتواريخ" swept them and "أحدث
        # تحديث" did not, though both ask for the same evidence; the measured result was
        # a definitive ruling that never entered the candidate pool at all.
        if analysis.needs_dated_sweep:
            self._add_dated_sections(candidates, document_ids)
        self._fuse(candidates)

        if not candidates or not self._has_coverage(analysis, candidates):
            logger.info("No candidate cleared the coverage bar for: %s", analysis.question[:60])
            return []

        ranked = self.reranker.rerank(analysis, list(candidates.values()), limit=limit * 3)
        if self.enable_expansion:
            self._expand(analysis, ranked, candidates, limit)

        final = self.reranker.rerank(analysis, list(candidates.values()), limit=limit)
        final = [c for c in final if c.rerank_score >= self.min_rerank_score]
        final = self._reserve_semantic_slots(analysis, candidates, final, limit)
        if analysis.intent is Intent.TIMELINE:
            final = self._reserve_timeline_slots(candidates, final, limit)
        elif analysis.temporal.is_temporal:
            final = self._reserve_recent_slots(analysis, candidates, final, limit)

        logger.info(
            "Retrieval: intent=%s pool=%s → %s selected (wide=%s)",
            analysis.intent, len(candidates), len(final), analysis.wants_wide_retrieval,
        )
        return final

    def _reserve_semantic_slots(
        self,
        analysis: QueryAnalysis,
        candidates: dict[str, Candidate],
        final: list[Candidate],
        limit: int,
    ) -> list[Candidate]:
        """Guarantee the strongest pure-similarity hits a place in the context.

        Lexical features can outweigh a strong semantic match and push it out of the
        selection — most visibly when the answer is written in the other language, where
        the question's words cannot match at all while unrelated same-language chunks
        score well on them. Reserving slots makes that failure impossible.

        A rescue, not an override. Passages the reranker deliberately demoted are not
        eligible: a version that a later correction replaced, and — unless the question
        is about the document itself — the document's own bookkeeping. This was measured.
        Asked for the expert's scope, the ranking put the passage stating the version in
        force at the top and the file's changelog near the bottom, and the reservation
        then reinstated the changelog to first place, where it was cited as [1] and
        offered the model all three superseded counts at once. A mechanism for rescuing
        buried matches must not resurrect what was buried on purpose.
        """
        if self.reserved_semantic_slots <= 0 or not final:
            return final

        wants_metadata = analysis.metadata_intent.is_metadata
        eligible = [
            c for c in candidates.values()
            if c.vector_score is not None
            and c.standing != "superseded"
            and (wants_metadata or not c.is_metadata)
        ]
        by_similarity = sorted(
            eligible, key=lambda c: c.vector_score or 0.0, reverse=True
        )[: self.reserved_semantic_slots]

        selected = {c.chunk_id for c in final}
        missing = [c for c in by_similarity if c.chunk_id not in selected]
        if not missing:
            return final

        keep = final[: max(limit - len(missing), 1)]
        logger.info(
            "Reserved %s semantic slot(s) the reranker had dropped", len(missing)
        )
        # Reinstated hits go first: they are the closest semantic matches in the whole
        # collection, and putting them last would let the context budget drop them again.
        for candidate in missing:
            candidate.origins.add("semantic-reserved")
        return missing + keep

    def _reserve_timeline_slots(
        self, candidates: dict[str, Candidate], final: list[Candidate], limit: int
    ) -> list[Candidate]:
        """Guarantee the dated sections a place when the question asks for a timeline.

        Those chunks are reached only through the date sweep, so they carry no vector or
        keyword score and the reranker ranks them last — which is how "آخر تحديثات"
        answered with one date out of four. Ordering is newest first, matching "آخر".
        """
        # One chunk per dated section, so the slots cover as many distinct dates as
        # possible instead of several chunks of the same event.
        per_section: dict[str, Candidate] = {}
        for candidate in sorted(
            (c for c in candidates.values() if c.timeline_rank is not None),
            key=lambda c: (c.timeline_rank, -c.rerank_score),
        ):
            per_section.setdefault(candidate.section_id, candidate)

        reserved = list(per_section.values())[: max(limit - 2, 1)]
        if not reserved:
            return final

        reserved_ids = {c.chunk_id for c in reserved}
        if reserved_ids <= {c.chunk_id for c in final}:
            return final

        rest = [c for c in final if c.chunk_id not in reserved_ids]
        logger.info("Reserved %s dated section(s) for a timeline question", len(reserved))
        return reserved + rest[: max(limit - len(reserved), 0)]

    def _reserve_recent_slots(
        self,
        analysis: QueryAnalysis,
        candidates: dict[str, Candidate],
        final: list[Candidate],
        limit: int,
    ) -> list[Candidate]:
        """Keep a few slots for the newest dated evidence on a "latest" question.

        Deliberately much smaller than the timeline reservation. A timeline question
        wants every dated event and is answered by enumerating them; "what is the latest
        position?" wants the newest one and is answered in a sentence. Reserving the same
        number of slots for both would turn every short question into a long
        chronological listing — a different wrong answer, not a fix.

        Three is enough for the decisive section plus the context immediately around it,
        and leaves the rest of the budget to the reranker, which is still the thing that
        knows whether a passage is about the right subject at all.
        """
        if not final or analysis.temporal.explicit_dates:
            return final

        window = analysis.temporal.window
        if window is not None:
            # The period the question named, not the newest material: "last week" asked
            # on a file whose newest entry is from last month has nothing to reserve,
            # and filling the slots with last month would answer a different question.
            dated = [c for c in candidates.values() if window.contains(c.section_date)]
        else:
            dated = [c for c in candidates.values() if c.timeline_rank is not None]
        if not dated:
            return final

        newest_first = analysis.temporal.wants_latest
        if window is not None:
            # Inside the window, newest first; a passage reached by the vector or keyword
            # arm has a date but no timeline rank, so the date itself is the key.
            def order(c: Candidate) -> tuple:
                return (tuple(-part for part in c.section_date), -c.rerank_score)
        else:
            # Ties broken by how well the passage answers the question, mirroring the
            # timeline reservation. Without the second key, sections sharing a date were
            # ordered by whatever the dictionary happened to hold first, and the one that
            # actually carried the ruling fell outside the reserved slots on some runs
            # and inside them on others.
            def order(c: Candidate) -> tuple:
                return (c.timeline_rank if newest_first else -c.timeline_rank, -c.rerank_score)
        per_section: dict[str, Candidate] = {}
        for candidate in sorted(dated, key=order):
            per_section.setdefault(candidate.section_id, candidate)

        reserved = list(per_section.values())[: self.recent_slots]
        selected = {c.chunk_id for c in final}
        missing = [c for c in reserved if c.chunk_id not in selected]
        if not missing:
            return final

        for candidate in missing:
            candidate.origins.add("recency-reserved")
        logger.info(
            "Reserved %s dated section(s) for a '%s' question",
            len(missing), analysis.temporal.cue or "temporal",
        )
        keep = final[: max(limit - len(missing), 1)]
        return missing + keep

    def _has_coverage(
        self, analysis: QueryAnalysis, candidates: dict[str, Candidate]
    ) -> bool:
        """Decide whether anything retrieved addresses the question at all.

        Deliberately generous: a false refusal is worse than passing thin evidence to a
        model whose prompt already makes it refuse. The bar is a semantic match above
        the similarity threshold, an exact identifier, or at least one meaningful query
        term appearing in the leading candidates. A bare BM25 hit is not enough on its
        own, because common words match almost any question.
        """
        if max((c.vector_score or 0.0) for c in candidates.values()) >= self.score_threshold:
            return True

        # Arabic stems are short — "عقد", "رقم" — so the bar is the same length the
        # rest of the pipeline uses, not something stricter.
        # The standard form, plus synonyms: a question about "الجدار" is addressed by a
        # passage about "السور", and refusing it for want of the literal word would be
        # the false refusal this check exists to avoid.
        query_terms = content_terms(analysis.search_text or analysis.question)
        if analysis.expansions:
            query_terms |= content_terms(" ".join(analysis.expansions))
        if not query_terms:
            return False

        identifiers = [normalize(i) for i in analysis.identifiers]
        best = sorted(candidates.values(), key=lambda c: c.fused_score, reverse=True)[
            : max(self.top_k, 8)
        ]

        covered: set[str] = set()
        for candidate in best:
            if identifiers and any(
                ident in normalize(f"{candidate.section} {candidate.content}")
                for ident in identifiers
            ):
                return True
            covered |= content_terms(candidate.content) | content_terms(candidate.section)

        return len(query_terms & covered) >= self.min_covered_terms

    # ---------------- candidate sources ----------------

    def _add_vector_hits(
        self,
        analysis: QueryAnalysis,
        candidates: dict[str, Candidate],
        category: str | None,
        document_ids: list[str] | None,
    ) -> None:
        # A colloquial question is embedded in its standard form, which is the register
        # the documents are written in; any other question is embedded as asked, exactly
        # as before.
        vector = self.cache.get_or_compute(
            analysis.normalized, lambda: self.embedder.embed_one(analysis.embedding_text)
        )
        hits = self.store.search(
            vector=vector,
            top_k=self.candidate_pool,
            score_threshold=self.vector_score_floor,
            document_ids=document_ids,
            category=category,
        )
        for rank, hit in enumerate(hits):
            chunk_id = str(hit.get("chunk_id", ""))
            if not chunk_id:
                continue
            candidate = candidates.get(chunk_id) or self._from_payload(hit)
            candidate.vector_score = float(hit.get("score", 0.0))
            candidate.vector_rank = rank
            candidate.origins.add("vector")
            candidates[chunk_id] = candidate

    def _add_keyword_hits(
        self,
        analysis: QueryAnalysis,
        candidates: dict[str, Candidate],
        document_ids: list[str] | None,
    ) -> None:
        results = self.keyword_index.search(
            analysis.search_text or analysis.question,
            limit=self.candidate_pool,
            document_ids=document_ids,
            expansions=analysis.expansions,
        )
        if not results:
            return
        records = self.knowledge.chunks_by_ids([chunk_id for chunk_id, _ in results])
        for rank, (chunk_id, score) in enumerate(results):
            record = records.get(chunk_id)
            if record is None:
                continue
            candidate = candidates.get(chunk_id) or self._from_record(record)
            candidate.keyword_score = score
            candidate.keyword_rank = rank
            candidate.origins.add("keyword")
            candidates[chunk_id] = candidate

    def _add_entity_hits(
        self,
        analysis: QueryAnalysis,
        candidates: dict[str, Candidate],
        document_ids: list[str] | None,
    ) -> None:
        """Pull in sections whose extracted facts match the wording of the question."""
        terms = analysis.keywords + analysis.identifiers
        entities = self.knowledge.find_entities(
            terms, kinds=list(analysis.preferred_entity_kinds), limit=40
        )
        if not entities:
            return

        by_document: dict[str, list[str]] = {}
        for entity in entities:
            if document_ids and entity.document_id not in document_ids:
                continue
            by_document.setdefault(entity.document_id, []).append(entity.section_id)

        for document_id, section_ids in by_document.items():
            for record in self.knowledge.chunks_in_sections(
                document_id, list(dict.fromkeys(section_ids))[:8], limit=12
            ):
                candidate = candidates.get(record.chunk_id) or self._from_record(record)
                candidate.origins.add("entity")
                candidates[record.chunk_id] = candidate

    def _add_dated_sections(
        self, candidates: dict[str, Candidate], document_ids: list[str] | None
    ) -> None:
        """Sweep the most recent dated sections of the documents already in play.

        A "latest updates" question names none of the words those sections use — their
        date lives in the heading — so no lexical or semantic arm reaches them.
        """
        scope = document_ids or sorted({c.document_id for c in candidates.values()})
        if not scope:
            return

        for rank, (document_id, section_id, _when) in enumerate(
            self.knowledge.dated_sections(scope, limit=self.timeline_sections)
        ):
            # One chunk per dated section: the slots are better spent covering more
            # distinct dates than several chunks of the same event.
            records = self.knowledge.chunks_in_sections(document_id, [section_id], limit=1)
            if not records:
                # These update sections are container headings that hold the date but no
                # body of their own; the text sits in their child sections, whose
                # breadcrumb still carries the parent's date.
                children = self.knowledge.related_section_ids(document_id, section_id)
                records = self.knowledge.chunks_in_sections(document_id, children[:1], limit=1)
            for record in records:
                candidate = candidates.get(record.chunk_id) or self._from_record(record)
                candidate.origins.add("timeline")
                if candidate.timeline_rank is None:
                    candidate.timeline_rank = rank
                candidates[record.chunk_id] = candidate

    # ---------------- fusion and expansion ----------------

    @staticmethod
    def _fuse(candidates: dict[str, Candidate]) -> None:
        """Reciprocal rank fusion — comparable across two very different score scales."""
        for candidate in candidates.values():
            score = 0.0
            if candidate.vector_rank is not None:
                score += 1.0 / (RRF_K + candidate.vector_rank + 1)
            if candidate.keyword_rank is not None:
                score += 1.0 / (RRF_K + candidate.keyword_rank + 1)
            if "entity" in candidate.origins:
                score += 1.0 / (RRF_K + 1)
            if "same-section" in candidate.origins:
                # Continuation of a section that already ranked well is real evidence,
                # but it earns less than a chunk found on its own merit.
                score += 1.0 / (RRF_K + 10)
            candidate.fused_score = score * RRF_K

    def _expand(
        self,
        analysis: QueryAnalysis,
        ranked: list[Candidate],
        candidates: dict[str, Candidate],
        limit: int,
    ) -> None:
        """Add neighbouring sections and chase query terms no candidate covers yet."""
        if not ranked:
            return

        budget = self.max_expanded_candidates - len(candidates)
        if budget <= 0:
            return

        # A long section is split across several chunks, so the rest of the answer is
        # most often in a neighbouring chunk of the *same* section — a timeline table,
        # for example. Fill those in before reaching for other sections.
        for seed in ranked[:3]:
            if budget <= 0:
                break
            for record in self.knowledge.chunks_in_sections(
                seed.document_id, [seed.section_id], limit=min(budget, 6)
            ):
                if record.chunk_id in candidates:
                    continue
                candidate = self._from_record(record)
                candidate.origins.add("same-section")
                candidates[record.chunk_id] = candidate
                budget -= 1

        if analysis.wants_wide_retrieval:
            for seed in ranked[:3]:
                if budget <= 0:
                    break
                related = self.knowledge.related_section_ids(seed.document_id, seed.section_id)
                for record in self.knowledge.chunks_in_sections(
                    seed.document_id, related[:6], limit=min(budget, 12)
                ):
                    if record.chunk_id in candidates:
                        continue
                    candidate = self._from_record(record)
                    candidate.origins.add("expansion")
                    candidates[record.chunk_id] = candidate
                    budget -= 1

        uncovered = self._uncovered_terms(analysis, ranked[:limit])
        if uncovered and budget > 0:
            logger.info("Expanding retrieval for uncovered terms: %s", sorted(uncovered)[:8])
            extra = self.keyword_index.search(" ".join(sorted(uncovered)), limit=min(budget, 12))
            records = self.knowledge.chunks_by_ids([chunk_id for chunk_id, _ in extra])
            for chunk_id, score in extra:
                record = records.get(chunk_id)
                if record is None or chunk_id in candidates:
                    continue
                candidate = self._from_record(record)
                candidate.keyword_score = score
                candidate.origins.add("gap-fill")
                candidates[chunk_id] = candidate

        self._fuse(candidates)

    @staticmethod
    def _uncovered_terms(analysis: QueryAnalysis, selected: list[Candidate]) -> set[str]:
        query_terms = {
            t for t in content_terms(analysis.search_text or analysis.question) if len(t) > 3
        }
        if not query_terms:
            return set()
        covered: set[str] = set()
        for candidate in selected:
            covered |= content_terms(candidate.content) | content_terms(candidate.section)
        # A term covered through a synonym is not a gap worth filling.
        alternatives = analysis.term_alternatives
        return {
            t for t in query_terms - covered
            if not (alternatives.get(t) and alternatives[t] & covered)
        }

    # ---------------- adapters ----------------

    @staticmethod
    def _from_payload(payload: dict) -> Candidate:
        return Candidate(
            chunk_id=str(payload.get("chunk_id", "")),
            document_id=str(payload.get("document_id", "")),
            filename=str(payload.get("filename", "")),
            document_title=str(payload.get("document_title") or payload.get("title", "")),
            section=str(payload.get("section", "")),
            section_id=str(payload.get("section_id", "")),
            heading=str(payload.get("heading", "")),
            parent_section=str(payload.get("parent_section", "")),
            content=str(payload.get("content", "")),
            version=str(payload.get("version", "")),
            category=str(payload.get("category", "")),
            language=str(payload.get("language", "")),
            has_table=bool(payload.get("has_table", False)),
            # Absent on every point indexed before locators existed, which is exactly
            # the Markdown case and reads as empty.
            locator=str(payload.get("locator") or ""),
            page=payload.get("page"),
        )

    @staticmethod
    def _from_record(record: ChunkRecord) -> Candidate:
        return Candidate(
            chunk_id=record.chunk_id,
            document_id=record.document_id,
            filename=record.filename,
            document_title=record.document_title,
            section=record.section,
            section_id=record.section_id,
            heading=record.heading,
            parent_section=record.parent_section,
            content=record.content,
            version=record.version,
            category=record.category,
            language=record.language,
            has_table=record.has_table,
            locator=record.locator or "",
            page=record.page,
        )
