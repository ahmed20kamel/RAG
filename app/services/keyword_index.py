"""BM25 keyword index over chunk text.

Vector search finds meaning; this finds the exact strings it misses — contract
numbers, licence numbers, clause references, names. Built in-process from the
chunks table so there is no extra service to run, and rebuilt when documents change.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.number_words import digit_forms
from app.core.text import normalize, tokenize
from app.models.database import session_scope
from app.models.knowledge import ChunkRecord

logger = logging.getLogger(__name__)

K1 = 1.4
B = 0.72


@dataclass(slots=True)
class _Posting:
    chunk_id: str
    document_id: str
    length: int
    frequencies: dict[str, int] = field(default_factory=dict)


class KeywordIndex:
    """In-memory BM25 over every indexed chunk. Rebuilt on ingest, delete and startup."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._postings: dict[str, _Posting] = {}
        self._inverted: dict[str, set[str]] = defaultdict(set)
        self._avg_length: float = 1.0
        self._loaded = False

    def rebuild(self) -> int:
        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(ChunkRecord).execution_options(yield_per=500)
                )
            )
            documents = [
                (row.chunk_id, row.document_id, self._document_text(row)) for row in rows
            ]

        count = self.build_from(documents)
        logger.info("Keyword index rebuilt: %s chunks, %s terms",
                    len(self._postings), len(self._inverted))
        return count

    def build_from(self, documents: list[tuple[str, str, str]]) -> int:
        """Build the index from (chunk_id, document_id, text) triples."""
        with self._lock:
            self._postings.clear()
            self._inverted = defaultdict(set)
            for chunk_id, document_id, text in documents:
                tokens = tokenize(text)
                if not tokens:
                    continue
                frequencies = Counter(tokens)
                self._postings[chunk_id] = _Posting(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    length=len(tokens),
                    frequencies=dict(frequencies),
                )
                for term in frequencies:
                    self._inverted[term].add(chunk_id)
            lengths = [p.length for p in self._postings.values()]
            self._avg_length = (sum(lengths) / len(lengths)) if lengths else 1.0
            self._loaded = True
        return len(self._postings)

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.rebuild()

    @staticmethod
    def _document_text(row: ChunkRecord) -> str:
        """Heading path is repeated into the searchable text so section names match too."""
        text = f"{row.document_title}\n{row.section}\n{row.heading}\n{row.content}"
        # A value written in words is indexed under its digits too, so "30 يومًا" in a
        # question finds "ثلاثون يومًا" in a clause. Stemming alone cannot: it folds
        # "ثلاثون" and "ثلاثة" onto the same stem, which is 30 and 3.
        spelled = digit_forms(row.content)
        return f"{text}\n{spelled}" if spelled else text

    def search(
        self,
        query: str,
        limit: int = 20,
        document_ids: list[str] | None = None,
        expansions: tuple[str, ...] | list[str] = (),
        expansion_weight: float = 0.5,
    ) -> list[tuple[str, float]]:
        """BM25 over the chunks. `expansions` — synonyms of the words asked — score at
        `expansion_weight` of a word actually asked, so a synonym can reach a passage the
        question's own words miss but never outrank one they hit."""
        self.ensure_loaded()
        spelled = digit_forms(query)
        terms = tokenize(f"{query} {spelled}" if spelled else query)
        if not terms:
            return []
        asked = set(terms)
        extra = {t for t in tokenize(" ".join(expansions)) if t not in asked} if expansions else set()
        factor = {t: 1.0 for t in asked} | {t: expansion_weight for t in extra}
        terms = list(asked | extra)

        allowed = set(document_ids) if document_ids else None

        with self._lock:
            total_docs = len(self._postings)
            if total_docs == 0:
                return []

            candidates: set[str] = set()
            for term in set(terms):
                candidates |= self._inverted.get(term, set())
            if not candidates:
                return []

            idf = {}
            for term in set(terms):
                doc_freq = len(self._inverted.get(term, ()))
                if doc_freq:
                    idf[term] = factor[term] * math.log(
                        1 + (total_docs - doc_freq + 0.5) / (doc_freq + 0.5)
                    )

            scored: list[tuple[str, float]] = []
            for chunk_id in candidates:
                posting = self._postings[chunk_id]
                if allowed and posting.document_id not in allowed:
                    continue
                score = 0.0
                norm = K1 * (1 - B + B * posting.length / self._avg_length)
                for term, weight in idf.items():
                    freq = posting.frequencies.get(term)
                    if not freq:
                        continue
                    score += weight * (freq * (K1 + 1)) / (freq + norm)
                if score > 0:
                    scored.append((chunk_id, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def unknown_terms(self, text: str) -> list[str]:
        """Words of `text` that occur in no indexed passage, in the order written.

        What a clarification is about: the word the asker used that the documents never
        do. Stopwords and very short tokens are excluded by `tokenize` already.
        """
        self.ensure_loaded()
        seen: list[str] = []
        with self._lock:
            for word in normalize(text).split():
                stems = tokenize(word)
                if stems and all(s not in self._inverted for s in stems) and word not in seen:
                    seen.append(word)
        return seen

    def knows(self, stem: str) -> bool:
        """Whether any indexed passage carries this stem or one of its forms — اليومي
        and اليومية reduce to stems one letter apart, and are the same word to a reader."""
        self.ensure_loaded()
        with self._lock:
            if stem in self._inverted:
                return True
            if len(stem) < 3:
                return False
            return any(t.startswith(stem) or (len(t) >= 3 and stem.startswith(t)) for t in self._inverted)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"chunks": len(self._postings), "terms": len(self._inverted)}
