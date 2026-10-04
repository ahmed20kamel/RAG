"""What the system learns from how people ask, with nobody approving each step.

Two habits, both private to the person who taught them:

* **Rephrasing.** A question that found nothing, followed minutes later by the same
  person asking again in other words and getting an answer, is the clearest synonym
  signal there is: the word they used first is not the word their files use. The pair
  becomes that person's own terminology, active at once, and their later questions are
  searched with both words.

* **Repeated questions.** The same question from the same person, asked again while
  their documents and knowledge are unchanged, is answered from memory instead of
  through the whole pipeline. Any upload, deletion or new knowledge changes the stamp the
  memory is keyed on, so a remembered answer is never older than the evidence it quotes.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections import OrderedDict

from sqlalchemy import func, select

from app.core.knowledge import KnowledgeScope, KnowledgeType
from app.models.database import session_scope
from app.models.document import Document
from app.models.knowledge_items import KnowledgeItem
from app.services.query_analysis import NORMALISED_INTERROGATIVES
from app.services.query_rewrite import canonicalize, personal_terms, term_statement

logger = logging.getLogger(__name__)

#: A second attempt after this long is a new question, not a rephrasing.
REPHRASE_WINDOW_SECONDS = 600


def _related(a: str, b: str) -> bool:
    """One word in two forms: equal, or one a prefix of the other past two letters."""
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def answered(response) -> bool:
    return bool(getattr(response, "grounded", False)) and getattr(response, "answer_source", "") not in ("none", "web") \
        and not getattr(response, "choices", None)


class RephraseLearner:
    def __init__(self, analyzer, keyword_index, knowledge_service, window: float = REPHRASE_WINDOW_SECONDS) -> None:
        self.analyzer = analyzer
        self.keyword_index = keyword_index
        self.knowledge = knowledge_service
        self.window = window
        self._unanswered: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def pair(self, failed: str, worked: str) -> tuple[str, str] | None:
        """(word asked, words the files use) — or None when the two questions do not
        differ by one unknown word swapped for one or two known ones.

        Compared as stems, and forms of one word count as the same word: اليومي in the
        first question and اليومية in the second are not a swap.
        """
        before = [k for k in self.analyzer.analyze(failed).keywords if k not in NORMALISED_INTERROGATIVES]
        after = [k for k in self.analyzer.analyze(worked).keywords if k not in NORMALISED_INTERROGATIVES]
        unknown = [
            k for k in before
            if not self.keyword_index.knows(k) and not any(_related(k, a) for a in after)
        ]
        if len(unknown) != 1:
            return None
        new = [a for a in after if not any(_related(a, b) for b in before) and self.keyword_index.knows(a)]
        if not 1 <= len(new) <= 2:
            return None
        return unknown[0], " ".join(new)

    def observe(self, user, question: str, response) -> tuple[str, str] | None:
        """Remember an unanswered question; learn from the answered one that follows."""
        if user is None:
            return None
        now = time.monotonic()
        with self._lock:
            if not answered(response):
                self._unanswered[user.id] = (now, question)
                return None
            previous = self._unanswered.pop(user.id, None)
        if previous is None or now - previous[0] > self.window:
            return None
        found = self.pair(previous[1], question)
        if found is None:
            return None
        asked, meant = found
        if any(a == asked for a, _ in personal_terms(user.id)):
            return None
        from app.services.knowledge_service import ProposedKnowledge

        with session_scope() as db:
            self.knowledge.propose(db, user, ProposedKnowledge(
                type=KnowledgeType.TERMINOLOGY,
                content=term_statement(asked, meant),
                scope=KnowledgeScope.USER,
                confidence=0.6,
                explanation="تعلّمه النظام من إعادة صياغة سؤال لم يجد إجابة إلى سؤال وجدها.",
                tags=("learned-from-rephrasing",),
            ))
        logger.info("Learned for %s: %s ≈ %s", user.email, asked, meant)
        return found


class AnswerMemory:
    """Answers to repeated questions, per person, valid while their evidence is unchanged."""

    def __init__(self, capacity: int = 500, max_age_seconds: float = 86_400) -> None:
        self.capacity = capacity
        self.max_age = max_age_seconds
        self._items: OrderedDict[tuple, tuple[str, float, object]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(user, question: str) -> tuple:
        return (getattr(user, "id", None) or "*", canonicalize(question).text)

    @staticmethod
    def stamp(owner_id: str | None) -> str:
        """Changes whenever anything an answer could rest on changes: the reader's
        documents, or any knowledge item."""
        with session_scope() as db:
            docs = select(func.count(Document.id), func.max(Document.updated_at))
            if owner_id is not None:
                docs = docs.where(Document.owner_id == owner_id)
            count, latest = db.execute(docs).one()
            k_count, k_latest = db.execute(select(func.count(KnowledgeItem.id), func.max(KnowledgeItem.updated_at))).one()
        return f"{count}|{latest}|{k_count}|{k_latest}"

    def get(self, user, question: str, stamp: str):
        key = self.key(user, question)
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            saved_stamp, saved_at, response = entry
            if saved_stamp != stamp or time.monotonic() - saved_at > self.max_age:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return copy.deepcopy(response)

    def put(self, user, question: str, stamp: str, response) -> None:
        if not answered(response):
            return
        with self._lock:
            self._items[self.key(user, question)] = (stamp, time.monotonic(), copy.deepcopy(response))
            self._items.move_to_end(self.key(user, question))
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
