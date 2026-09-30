"""Turns a question into an answer contract.

The mapping is grammatical: an interrogative implies the sort of value that answers it,
and its number implies whether one value is enough. "من هو الخبير" owes a person;
"من هم الأطراف" owes every person the evidence supports. Nothing here inspects what the
question is *about*, so it behaves the same on a contract, a case file or a method
statement.
"""

from __future__ import annotations

import logging
import re

from app.core.contract import AnswerContract, Requirement, RequirementKind
from app.core.text import ARABIC_PUNCTUATION, content_terms, normalize
from app.services.query_analysis import Intent, QueryAnalysis

logger = logging.getLogger(__name__)

# Arabic punctuation sits inside the Arabic Unicode block, so a range over that block
# treats "؟" as a letter: a cue at the very end of a question would never match, and
# "ومتى؟" would never read as a new clause. Exclude the punctuation from the class.
_NOT_LETTER = rf"(?![\w؀-ۿ])|(?=[{ARABIC_PUNCTUATION}])"
WORD_START = r"(?<![\w؀-ۿ])"
WORD_END = rf"(?:{_NOT_LETTER})"


def _cues(*phrases: str) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(normalize(p)) for p in sorted(phrases, key=len, reverse=True)
    )
    return re.compile(f"{WORD_START}(?:{alternatives}){WORD_END}")


# Interrogatives and the kind of value each one asks for. Plural forms additionally
# mean "list them all", which is what PLURAL_ASK captures.
def _ask(phrases: tuple[str, ...], kind: RequirementKind):
    """A cue group matched both literally and by stem."""
    stems = set()
    for phrase in phrases:
        stems |= {t for t in content_terms(phrase) if len(t) > 2}
    return _cues(*phrases), stems, kind


ASKS: tuple[tuple[re.Pattern[str], set[str], RequirementKind], ...] = (
    _ask(("من", "who", "whom"), RequirementKind.PERSON),
    _ask(("متى", "when", "تاريخ", "بتاريخ", "تواريخ", "date of"), RequirementKind.DATE),
    _ask(("كم", "how much", "how many", "رقم", "قيمة", "مبلغ", "نسبة", "عدد", "مقدار"),
         RequirementKind.NUMBER),
    _ask(("ماذا", "ما الذي", "what happened", "نتج", "حدث", "جرى", "نتيجة", "نتائج"),
         RequirementKind.EVENT),
    _ask(("اين", "where", "مكان", "موقع"), RequirementKind.ATTRIBUTE),
)

PLURAL_ASK = _cues(
    "من هم", "من هن", "who are", "الاطراف", "الاشخاص", "الشركات", "المستندات",
    "الاجتماعات", "الجلسات", "العناصر", "البنود",
)
LIST_ASK = _cues("جميع", "كل", "اذكر", "اسرد", "قائمه", "list all", "enumerate", "عدد لي")
ORG_HINT = _cues("شركه", "شركات", "مؤسسه", "جهه", "company", "companies")
ORG_STEMS = {t for p in ("شركة", "شركات", "مؤسسة", "جهة", "company", "companies")
             for t in content_terms(p)}

# Splits a question into the separate things it asks. A part is a clause introduced by
# its own interrogative; anything shorter than this is a fragment, not a request.
# Spelling variants are listed because the split runs on the question as written, before
# normalisation, so "أين" and "اين" both have to be recognised.
INTERROGATIVES = (
    "ماذا", "لماذا", "متى", "متي", "أين", "اين", "كيف", "هل", "ما", "من", "كم",
)
_ASK_AHEAD = "|".join(sorted(INTERROGATIVES, key=len, reverse=True))
PART_SPLIT = re.compile(
    r"\s*[،؛,]\s*"
    rf"|\s+و(?=\s*(?:{_ASK_AHEAD})(?:{_NOT_LETTER}))"
    r"|\s+and(?=\s+(?:what|who|when|where|how|why)\b)"
)
MIN_PART_CHARS = 6
MAX_REQUIREMENTS = 6


class AnswerContractBuilder:
    def build(self, analysis: QueryAnalysis) -> AnswerContract:
        parts = self._parts(analysis.question)
        requirements: list[Requirement] = []

        for index, part in enumerate(parts):
            normalised = normalize(part)
            enumeration = self._is_enumeration(normalised, analysis, len(parts))
            for kind in self._kinds(normalised):
                requirements.append(
                    Requirement(
                        key=f"p{index}.{kind}",
                        kind=self._refine(kind, normalised),
                        part=part.strip(),
                        terms={t for t in content_terms(part) if len(t) > 2},
                        enumeration=enumeration,
                    )
                )

        # A question whose wording carries no interrogative at all still owes whatever
        # its own words point at; treat it as one open attribute requirement.
        if not requirements and analysis.question.strip():
            requirements.append(
                Requirement(
                    key="p0.attribute",
                    kind=RequirementKind.ATTRIBUTE,
                    part=analysis.question.strip(),
                    terms={t for t in content_terms(analysis.question) if len(t) > 2},
                    enumeration=analysis.exhaustive,
                )
            )

        chronological = analysis.intent is Intent.TIMELINE
        # A question about a sequence owes dates whether or not its wording names one.
        if chronological and not any(
            r.kind is RequirementKind.DATE for r in requirements
        ):
            requirements.insert(
                0,
                Requirement(
                    key="p0.date",
                    kind=RequirementKind.DATE,
                    part=analysis.question.strip(),
                    terms={t for t in content_terms(analysis.question) if len(t) > 2},
                    enumeration=True,
                ),
            )

        contract = AnswerContract(
            question=analysis.question,
            requirements=requirements[:MAX_REQUIREMENTS],
            multi_part=len(parts) > 1,
            chronological=chronological,
        )
        logger.info(
            "Contract: %s requirement(s) over %s part(s) — %s",
            len(contract.requirements), len(parts),
            [r.key for r in contract.requirements],
        )
        return contract

    @classmethod
    def _parts(cls, question: str) -> list[str]:
        pieces = []
        for piece in PART_SPLIT.split(question):
            if not piece or not piece.strip():
                continue
            # A clause after a comma keeps the conjunction glued to its interrogative
            # ("ومتى"), which would hide the interrogative from the cue patterns.
            cleaned = re.sub(r"^\s*و(?=[^\s])", "", piece.strip()).strip(" ؟?.،")
            if cleaned:
                pieces.append(cleaned)
        # A part may be nothing but its interrogative ("and when?") and still be an ask.
        meaningful = [
            p for p in pieces
            if len(p) >= MIN_PART_CHARS or cls._kinds(normalize(p)) != [RequirementKind.ATTRIBUTE]
        ]
        return meaningful or [question.strip()]

    @staticmethod
    def _kinds(normalised_part: str) -> list[RequirementKind]:
        # Cues are matched against the words as written and against their stems, so a
        # question saying "بالتواريخ" still reaches the cue "تاريخ".
        stems = content_terms(normalised_part)
        kinds = [
            kind for pattern, cue_stems, kind in ASKS
            if pattern.search(normalised_part) or (cue_stems & stems)
        ]
        # Keep the order stable and drop repeats while preserving the first occurrence.
        return list(dict.fromkeys(kinds))[:2] or [RequirementKind.ATTRIBUTE]

    @staticmethod
    def _refine(kind: RequirementKind, normalised_part: str) -> RequirementKind:
        """A "who" about companies is an organisation question, not a person question."""
        if kind is not RequirementKind.PERSON:
            return kind
        if ORG_HINT.search(normalised_part) or (ORG_STEMS & content_terms(normalised_part)):
            return RequirementKind.ORGANIZATION
        return kind

    @staticmethod
    def _is_enumeration(normalised_part: str, analysis: QueryAnalysis, parts: int) -> bool:
        """Does *this* ask want every supported value, or one?

        ``analysis.exhaustive`` is measured over the whole question, so in a question
        that asks several things it says nothing about any one of them: a list cue in the
        third part would otherwise turn the first part — a single, specific ask — into a
        demand for every value the evidence carries. Each part is its own ask, so a
        multi-part question reads each part's own wording; a single-part question is the
        question, and keeps the question-level signal.
        """
        own_cue = bool(
            PLURAL_ASK.search(normalised_part) or LIST_ASK.search(normalised_part)
        )
        if parts > 1:
            return own_cue
        return bool(analysis.exhaustive or own_cue)
