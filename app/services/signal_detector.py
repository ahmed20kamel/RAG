"""Noticing when someone is teaching rather than asking — deterministically.

Two constraints shape everything here.

No model is called. Detection runs on every message, and a second generation per turn
would double the cost of a conversation to decide something patterns can decide. The
cue tables below are matched over the same normalised Arabic used by the rest of the
system, so "تعلّم" and "تعلم" are one cue and a trailing "؟" never hides one.

Nothing is written. A detection produces a *suggestion* the person is shown and must
accept; it never creates knowledge, and accepting it produces a PENDING proposal rather
than anything active. Detection is the cheapest place in the system to be wrong, which
is exactly why it is given no authority.

The cues are phrasings, not subjects. Nothing here knows what any document, project or
question is about, so a cue that worked for one topic works for every topic or for none.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.core.knowledge import KnowledgeType
from app.core.text import ARABIC_PUNCTUATION, normalize

logger = logging.getLogger(__name__)

# Arabic punctuation lives inside the Arabic Unicode block, so a plain \w boundary treats
# "؟" as a letter and a cue at the end of a sentence never matches. Same class of bug as
# in the contract builder; fixed the same way.
_NOT_LETTER = rf"(?![\w؀-ۿ])|(?=[{ARABIC_PUNCTUATION}])"
_WORD_START = r"(?<![\w؀-ۿ])"


def _cues(*phrases: str) -> re.Pattern[str]:
    """Cue alternatives, matched over normalised text.

    A cue that ends in a one-letter Arabic preposition — "يقصد بـ" — is written
    joined to the word it governs, so requiring a word boundary after it would mean
    it never fires on the phrasing people actually use.
    """
    alternatives = "|".join(
        re.escape(normalize(p)) for p in sorted(phrases, key=len, reverse=True)
    )
    boundary = rf"(?:{_NOT_LETTER})"
    joined = rf"(?<=[بلك])"  # ب ل ك attach directly to the next word
    return re.compile(f"{_WORD_START}(?:{alternatives})(?:{boundary}|{joined})")


# Ordered by how specific each signal is. A correction is checked before a plain fact
# because "المعلومة السابقة غير صحيحة، الصحيح هو X" contains both, and the more specific
# reading is the right one.
CORRECTION_CUES = _cues(
    "المعلومة السابقة غير صحيحة", "الاجابة السابقة خاطئة", "الاجابة غير صحيحة",
    "هذا غير صحيح", "غير صحيح", "خطا", "الصحيح هو", "التصحيح", "صحح",
    "that is incorrect", "this is wrong", "that is wrong", "the correct value is",
    "actually it is", "correction",
)

RULE_CUES = _cues(
    "تعلم القاعده", "تعلم القاعدة", "القاعده هي", "من الان فصاعدا", "من الان",
    "دائما اعرض", "دائما اذكر", "لا تختر", "يجب دائما", "كلما",
    "learn this rule", "from now on", "always show", "always list", "never choose",
    "whenever", "as a rule",
)

PREFERENCE_CUES = _cues(
    "افضل", "اريد الاجابه", "اجب باختصار", "اجب بالعربيه", "اجب بالانجليزيه",
    "اختصر", "بالتفصيل", "اسلوبي",
    "i prefer", "i'd prefer", "answer briefly", "keep it short", "be concise",
    "answer in arabic", "answer in english", "in detail",
)

TERMINOLOGY_CUES = _cues(
    "يقصد ب", "المقصود ب", "نعني ب", "يسمى", "مصطلح", "تعريف",
    "we mean by", "the term", "is defined as", "refers to", "stands for",
)

PROCEDURE_CUES = _cues(
    "الاجراء هو", "الخطوات هي", "اتبع الخطوات", "اولا ثم", "طريقه العمل",
    "the procedure is", "the steps are", "follow these steps", "the process is",
)

FACT_CUES = _cues(
    "تعلم ان", "احفظ ان", "للعلم", "المعلومه هي", "الاسم الرسمي", "القيمه الصحيحه",
    "سجل ان", "اعتمد ان",
    "learn that", "remember that", "note that", "for the record", "the official name is",
)

#: Checked in order; the first match wins, so a specific reading beats a general one.
SIGNALS: tuple[tuple[re.Pattern[str], KnowledgeType, str], ...] = (
    (CORRECTION_CUES, KnowledgeType.CORRECTION, "correction"),
    (RULE_CUES, KnowledgeType.RULE, "rule"),
    (PROCEDURE_CUES, KnowledgeType.PROCEDURE, "procedure"),
    (TERMINOLOGY_CUES, KnowledgeType.TERMINOLOGY, "terminology"),
    (PREFERENCE_CUES, KnowledgeType.PREFERENCE, "preference"),
    (FACT_CUES, KnowledgeType.FACT, "fact"),
)

#: Phrases that introduce the substance, so the suggestion can start there instead of
#: repeating the cue back at the person.
_LEAD_INS = (
    "الصحيح هو", "القيمه الصحيحه هي", "المعلومه هي", "تعلم القاعده", "تعلم القاعدة",
    "تعلم ان", "احفظ ان", "سجل ان", "اعتمد ان", "القاعده هي", "الاجراء هو",
    "الخطوات هي", "يقصد ب", "المقصود ب", "نعني ب",
    "the correct value is", "actually it is", "learn that", "remember that",
    "note that", "the rule is", "the procedure is", "we mean by",
)

MIN_CONTENT_CHARS = 12
MAX_CONTENT_CHARS = 1000

#: Punctuation and joiners left behind once a cue is cut off the front, including the
#: Arabic tatweel (U+0640) and the quotation marks a definition usually opens with.
_TRIM = " \t\n\r،,:؛—-.«»ـ"


@dataclass(slots=True)
class LearningSignal:
    """What was noticed, and what it would become if the person agrees."""

    type: KnowledgeType
    #: The cue family that fired — reported so a person can see why they were asked.
    signal: str
    #: The wording lifted from the message, trimmed of the cue that introduced it.
    suggested_content: str
    raw_text: str
    #: How clearly the phrasing matched. It ranks suggestions; it grants nothing.
    confidence: float = 0.5

    @property
    def needs_approval(self) -> bool:
        """Only a personal preference can skip review, and only for its owner."""
        return self.type is not KnowledgeType.PREFERENCE


class SignalDetector:
    """Finds teaching intent in a message. Never writes, never decides."""

    def detect(self, message: str) -> LearningSignal | None:
        text = (message or "").strip()
        if len(text) < MIN_CONTENT_CHARS or len(text) > MAX_CONTENT_CHARS:
            return None

        normalised = normalize(text)
        for pattern, knowledge_type, name in SIGNALS:
            match = pattern.search(normalised)
            if match is None:
                continue

            content = self._substance(text, normalised, match.end())
            if len(content) < MIN_CONTENT_CHARS:
                # A cue with nothing after it is a remark, not a lesson.
                continue

            signal = LearningSignal(
                type=knowledge_type,
                signal=name,
                suggested_content=content,
                raw_text=text,
                confidence=self._confidence(name, match, normalised),
            )
            logger.info(
                "Learning signal: %s (%s) from a %s-char message",
                name, knowledge_type, len(text),
            )
            return signal
        return None

    @classmethod
    def _substance(cls, original: str, normalised: str, cue_end: int) -> str:
        """The claim itself, with the phrase that introduced it removed.

        The cue was matched against the normalised text, whose indexes do not line up
        with the original — normalisation drops diacritics and tatweel, so every one of
        them shifts the two apart. The offset is mapped back before slicing, which is
        what keeps the suggestion readable as the person actually wrote it.
        """
        cut = cls._original_offset(original, cue_end)
        tail = original[cut:].strip(_TRIM)
        candidate = tail or original

        lowered = normalize(candidate)
        for lead in _LEAD_INS:
            position = lowered.find(lead)
            if position == -1:
                continue
            trimmed = candidate[
                cls._original_offset(candidate, position + len(lead)) :
            ].strip(_TRIM)
            if len(trimmed) >= MIN_CONTENT_CHARS:
                candidate = trimmed
            break
        return candidate.strip()

    @staticmethod
    def _original_offset(original: str, normalised_offset: int) -> int:
        """Where `normalised_offset` falls in the untouched text.

        Walks both strings together, normalising one character at a time, so a dropped
        diacritic advances the original without advancing the normalised counterpart.
        """
        if normalised_offset <= 0:
            return 0
        # Normalising the whole prefix, not one character at a time: `normalize` trims
        # and collapses whitespace, so a lone space measured on its own comes back empty
        # and the count drifts by one per word. The sentinel keeps the trailing space
        # from being stripped off the end of the prefix.
        for index in range(1, len(original) + 1):
            if len(normalize(original[:index] + "ء")) - 1 >= normalised_offset:
                return index
        return len(original)

    @staticmethod
    def _confidence(name: str, match: re.Match[str], normalised: str) -> float:
        """How clearly this reads as teaching. Ranks suggestions; grants nothing.

        A cue at the start of a message is a stronger signal than the same words buried
        mid-sentence, where they are more often part of a question.
        """
        base = {
            "correction": 0.8, "rule": 0.75, "procedure": 0.65,
            "terminology": 0.6, "preference": 0.7, "fact": 0.55,
        }[name]
        if match.start() <= 3:
            base += 0.1
        if normalised.rstrip().endswith("?") or "؟" in normalised:
            # A question mark suggests they are asking, not instructing.
            base -= 0.25
        return round(max(0.05, min(base, 0.95)), 2)
