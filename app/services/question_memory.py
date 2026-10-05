"""Every question kept, and three things learned from it — each private to the asker.

* **Follow-ups.** "وكم مدته؟" after "ما قيمة العقد؟" means the contract's duration. A
  question that reads as a continuation — it opens with "و…" or "طيب", or carries no more
  than one word of its own — is searched together with the question it follows, inside
  the documents that question was answered from.

* **Repeats.** A question asked again, while the reader's documents and knowledge are
  as they were, is answered with what they were told before — unless they marked that
  answer wrong. Kept in the database, so a restart forgets nothing.

* **Wordings that worked.** A question that found nothing, followed in the same
  conversation by one that found the answer, is remembered as a pair. Asked again in the
  first wording, it is searched in the second.

Nothing is read across people: every lookup is by the asker's own id.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from app.models.database import session_scope
from app.models.question_log import QuestionRecord
from app.services.learning_loop import answered
from app.services.query_analysis import NORMALISED_INTERROGATIVES, language_of
from app.services.query_rewrite import canonicalize

logger = logging.getLogger(__name__)

#: Openings that continue the previous question rather than start a new one.
FOLLOW_UP_OPENINGS = re.compile(
    r"^\s*(?:و\s*(?:كم|ما|ماذا|متى|هل|لماذا|ليش|اين|أين|كيف|من|ايش|إيش|شو|بالنسبة)"
    r"|طيب|طب|يعني|ماذا عن|وماذا عن|and\b|what about\b|how about\b)",
    re.IGNORECASE,
)
#: Words that carry no subject of their own.
FILLERS = {"طيب", "طب", "يعني", "بس", "كمان", "ايضا", "أيضا", "ممكن", "لو", "سمحت"}
#: Question words stripped from a subject before it is attached to a follow-up.
LEADING_QUESTION = re.compile(
    r"^\s*(?:ما\s+هي|ما\s+هو|ماذا|ما|كم|هل|متى|أين|اين|كيف|من|لماذا|what|how|when|is|are)\s+",
    re.IGNORECASE,
)
#: Words that point at something shown rather than name it.
POINTING = re.compile(
    r"\b(?:this|these|that|here|attached|picture|image|screenshot)\b|هذا|هذه|هذي|هنا|دا|ده|دي|فيها|فيه|الصورة|المرفق",
    re.IGNORECASE,
)
#: Clicked from a refusal's suggestions: the same question, confined to one file.
SCOPED = " في ملف "


@dataclass
class Prepared:
    """The question as it will be searched, and why it differs from what was asked."""

    question: str
    document_ids: list[str] | None = None
    notes: list[str] = field(default_factory=list)
    #: The question the conversation is about, when this one follows it.
    subject: str = ""

    @property
    def follow_up(self) -> bool:
        return bool(self.subject)


class QuestionMemory:
    def __init__(self, analyzer) -> None:
        self.analyzer = analyzer

    # -- reading -----------------------------------------------------------
    def _own_words(self, question: str) -> list[str]:
        return [
            k for k in self.analyzer.analyze(question).keywords
            if k not in NORMALISED_INTERROGATIVES and k not in FILLERS
        ]

    def is_follow_up(self, question: str) -> bool:
        return bool(FOLLOW_UP_OPENINGS.match(question)) or len(self._own_words(question)) <= 1

    @staticmethod
    def subject_of(record: QuestionRecord) -> str:
        """What a turn was about: its own question, or — for a follow-up — the question
        the chain started from."""
        return record.subject or record.question

    @staticmethod
    def attach(question: str, subject: str) -> str:
        """One question, not two: "وكم مدته بخصوص قيمة عقد المقاولة؟". Written as a
        sentence because a bracketed "(السؤال السابق: …؟)" was read as a second part,
        and answered as one."""
        topic = LEADING_QUESTION.sub("", subject.strip()).strip().rstrip("؟?. ")
        if language_of(question) == "en":
            return f"{question.strip().rstrip('؟?. ')} regarding {topic}?"
        return f"{question.strip().rstrip('؟?. ')} بخصوص {topic}؟"

    def prepare(self, user, question: str, conversation_id: str | None, has_scope: bool,
                pictured: bool = False) -> Prepared:
        """The question to search, given everything this person asked before.

        A picture sent into a conversation, with "this" in the words that go with it or
        after a question that went unanswered, is about that question: "the rates are
        clearly visible here" is the evidence for what was just asked.
        """
        if user is None:
            return Prepared(question)
        try:
            with session_scope() as db:
                previous = self._last_turn(db, user.id, conversation_id)
                continues = self.is_follow_up(question) or (pictured and previous is not None and (
                    previous.outcome != "answered" or POINTING.search(question)
                ))
                if previous is not None and continues:
                    subject = self.subject_of(previous)
                    scope = None
                    if not has_scope and previous.outcome == "answered" and previous.document_ids:
                        scope = list(previous.document_ids)
                    return Prepared(
                        self.attach(question, subject),
                        document_ids=scope,
                        notes=[f"سؤال متابعة ← فُهم في سياق: «{subject}»"],
                        subject=subject,
                    )
                worked = self._wording_that_worked(db, user.id, question)
                if worked is not None:
                    return Prepared(worked, notes=[f"صياغة تعلّمها النظام من محاولتك السابقة ← «{worked}»"])
        except Exception:  # noqa: BLE001 - asked afresh rather than not at all
            logger.exception("Could not read the question log")
        return Prepared(question)

    @staticmethod
    def _last_turn(db, user_id: str, conversation_id: str | None) -> QuestionRecord | None:
        if not conversation_id:
            return None
        return db.scalars(
            select(QuestionRecord)
            .where(QuestionRecord.user_id == user_id, QuestionRecord.conversation_id == conversation_id)
            .order_by(QuestionRecord.id.desc())
            .limit(1)
        ).first()

    @staticmethod
    def _wording_that_worked(db, user_id: str, question: str) -> str | None:
        canonical = canonicalize(question).text
        failed = db.scalars(
            select(QuestionRecord)
            .where(
                QuestionRecord.user_id == user_id,
                QuestionRecord.canonical == canonical,
                QuestionRecord.resolved_by.is_not(None),
            )
            .order_by(QuestionRecord.id.desc())
            .limit(1)
        ).first()
        if failed is None:
            return None
        resolved = db.get(QuestionRecord, failed.resolved_by)
        if resolved is None or resolved.feedback == "down" or resolved.outcome != "answered":
            return None
        return resolved.searched_as or resolved.question

    def recall(self, user, question: str, stamp: str):
        """The answer this person was given to this question, if nothing it rests on
        has changed and they did not mark it wrong."""
        from app.schemas.chat import ChatResponse

        if user is None or not stamp:
            return None
        try:
            with session_scope() as db:
                record = db.scalars(
                    select(QuestionRecord)
                    .where(
                        QuestionRecord.user_id == user.id,
                        QuestionRecord.canonical == canonicalize(question).text,
                        QuestionRecord.outcome == "answered",
                    )
                    .order_by(QuestionRecord.id.desc())
                    .limit(1)
                ).first()
                if record is None or record.stamp != stamp or record.feedback == "down" or not record.response:
                    return None
                # A follow-up's answer belongs to its conversation: "وكم مدته؟" means
                # something else after a different question.
                if record.subject:
                    return None
                return ChatResponse.model_validate(record.response)
        except Exception:  # noqa: BLE001
            logger.exception("Could not recall a stored answer")
            return None

    # -- writing -----------------------------------------------------------
    def record(self, user, question: str, conversation_id: str | None, prepared: Prepared | None,
               response, stamp: str | None, reusable: bool = True) -> None:
        """Keep the question. `reusable=False` — a question about an attached picture —
        keeps it without the answer a later bare repeat could be given."""
        if user is None:
            return
        outcome = self.outcome(response)
        searched_as = prepared.question if prepared is not None and prepared.question != question else ""
        try:
            with session_scope() as db:
                previous = self._last_turn(db, user.id, conversation_id)
                row = QuestionRecord(
                    user_id=user.id,
                    conversation_id=conversation_id or "",
                    question=question,
                    canonical=canonicalize(question).text,
                    searched_as=searched_as,
                    subject=prepared.subject if prepared is not None else "",
                    outcome=outcome,
                    answer=getattr(response, "answer", "") or "",
                    response=response.model_dump(mode="json") if outcome == "answered" and reusable else {},
                    document_ids=sorted({s.document_id for s in getattr(response, "sources", []) or []
                                         if getattr(s, "document_id", "")}),
                    stamp=stamp or "",
                    answer_id=getattr(response, "answer_id", "") or "",
                )
                db.add(row)
                db.flush()
                if outcome == "answered" and previous is not None and self._resolves(previous, question):
                    previous.resolved_by = row.id
                    logger.info("Learned a wording that works: «%s» → «%s»", previous.question[:60], question[:60])
        except Exception:  # noqa: BLE001 - an answer is never lost to its record
            logger.exception("Could not record the question")

    def _resolves(self, previous: QuestionRecord, question: str) -> bool:
        """Whether this answered question is the earlier, unanswered one asked again.

        Only the very next turn of the same conversation, and only when it keeps some of
        the first question's words — or is that question confined to a file, as a
        clicked suggestion is. An unrelated question that happens to come next would
        otherwise become what the first wording is searched as.
        """
        if previous.outcome == "answered" or previous.resolved_by is not None:
            return False
        if question.startswith(previous.question.rstrip("؟? ")) and SCOPED in question:
            return True
        before = set(self._own_words(previous.question))
        after = set(self._own_words(question))
        if not before or not after or self.is_follow_up(question):
            return False
        shared = {b for b in before if any(a == b or a.startswith(b) or b.startswith(a) for a in after)}
        return len(shared) / len(before) >= 0.34

    @staticmethod
    def outcome(response) -> str:
        if answered(response):
            return "answered"
        if getattr(response, "choices", None):
            return "clarified"
        if getattr(response, "answer_source", "") == "web":
            return "web"
        return "refused"

    def feedback(self, user, answer_id: str, value: str) -> list[str]:
        """Thumbs from the reader. A thumbs-down answer is never given out again.

        Every record of that answer is marked — a repeat given from memory carries the
        original's id — and the questions it answered are returned, so the caller can
        drop them from the fast memory too.
        """
        with session_scope() as db:
            records = list(db.scalars(
                select(QuestionRecord).where(
                    QuestionRecord.user_id == user.id, QuestionRecord.answer_id == answer_id
                )
            ))
            for record in records:
                record.feedback = value
            return sorted({q for r in records for q in (r.question, r.searched_as) if q})
