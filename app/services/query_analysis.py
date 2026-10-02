"""Query understanding — rule based, no model call.

Runs in well under a millisecond, which matters because the answer model already
costs over a minute on this hardware. Its output steers how wide retrieval goes
and which kinds of extracted facts to prefer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.text import IDENTIFIER, detect_language, normalize, tokenize
from app.services.evidence_class import EvidenceClass, query_wants_metadata
from app.services.query_rewrite import SynonymLexicon, canonicalize
from app.services.temporal import TemporalIntent, read_intent


class Intent(StrEnum):
    FACT = "fact"
    NUMERIC = "numeric"
    DATE = "date"
    ENTITY = "entity"
    COMPARISON = "comparison"
    TIMELINE = "timeline"
    AGGREGATION = "aggregation"
    PROCEDURE = "procedure"
    REASON = "reason"


INTENT_CUES: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.COMPARISON, ("قارن", "الفرق بين", "مقارنة", "مقابل", "compare", "difference between", "versus")),
    # "آخر تحديثات … بالتواريخ" is a timeline question even though it says neither
    # "تسلسل" nor "chronology"; without these cues it was treated as a single-fact
    # lookup and retrieved eight chunks instead of sweeping the dated sections.
    (Intent.TIMELINE, (
        "تسلسل", "التسلسل الزمني", "ترتيب الاحداث", "منذ", "حتى",
        "بالتواريخ", "بالتاريخ", "اخر تحديثات", "اخر التحديثات", "تحديثات",
        "المستجدات", "اخر المستجدات", "مستجدات", "تطورات", "اخر تطورات",
        "ماذا استجد", "ما استجد", "سير القضيه", "مراحل",
        "timeline", "chronology", "sequence of events", "latest updates", "recent updates",
    )),
    (Intent.AGGREGATION, ("جميع", "كل", "اذكر", "ما هي البنود", "قائمة", "list all", "all the", "enumerate")),
    (Intent.PROCEDURE, ("اجراءات", "كيف", "خطوات", "procedure", "how do", "how to", "steps")),
    (Intent.REASON, ("لماذا", "سبب", "لماذا تعد", "why", "reason")),
    (Intent.DATE, ("متى", "تاريخ", "بتاريخ", "when", "date of")),
    (Intent.NUMERIC, ("كم", "ما قيمة", "ما نسبة", "ما مبلغ", "ما عدد", "ما مقدار", "how much", "how many", "what is the value", "amount", "rate")),
    (Intent.ENTITY, ("من هم", "من هو", "من هي", "من يمثل", "الاطراف", "who is", "who are", "which company", "parties")),
)

KIND_PREFERENCE: dict[Intent, tuple[str, ...]] = {
    Intent.NUMERIC: ("amount", "percentage", "duration", "identifier", "attribute"),
    Intent.DATE: ("date", "attribute"),
    Intent.ENTITY: ("person", "organization", "attribute"),
    Intent.TIMELINE: ("date", "attribute"),
    Intent.COMPARISON: ("amount", "percentage", "duration", "attribute"),
    Intent.AGGREGATION: ("attribute", "identifier", "amount"),
    Intent.PROCEDURE: ("clause", "duration", "attribute"),
    Intent.REASON: ("attribute",),
    Intent.FACT: ("attribute", "identifier"),
}

INTERROGATIVES = ("من", "كم", "متى", "اين", "لماذا", "كيف", "ما", "ماهي", "هل",
                  "who", "what", "when", "where", "why", "how", "which")

# Questions that must enumerate everything the evidence supports rather than
# summarise. Missing one supported item is a wrong answer, not a shorter one.
EXHAUSTIVE_CUES = (
    "من هم", "من هن", "ما هي الشركات", "ما هي الاطراف", "من حضر", "من الحاضرون",
    "اذكر جميع", "اذكر كل", "جميع", "كل الاطراف", "قائمة", "عدد لي", "اسرد",
    "who are", "list all", "all the parties", "enumerate",
)

# Questions asking which person or company holds a specific role. The answer must
# keep each role attached to its own holder.
ROLE_CUES = (
    "الممثل القانوني", "من يمثل", "من يمثلها", "ما دور", "ما هو دور", "المسؤول عن",
    "صاحب", "مالك", "بصفته", "بصفة", "role of", "who represents", "responsible for",
)

WIDE_INTENTS = frozenset(
    {Intent.COMPARISON, Intent.TIMELINE, Intent.AGGREGATION, Intent.PROCEDURE, Intent.ENTITY}
)

WORD_START = r"(?<![\w؀-ۿ])"
WORD_END = r"(?![\w؀-ۿ])"


def _phrase_pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    """Cues must match whole words — 'الية' inside 'الإجمالية' is not a procedure question."""
    alternatives = "|".join(re.escape(normalize(p)) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(f"{WORD_START}(?:{alternatives}){WORD_END}")


INTENT_PATTERNS: tuple[tuple[Intent, re.Pattern[str]], ...] = tuple(
    (intent, _phrase_pattern(cues)) for intent, cues in INTENT_CUES
)

QUESTION_WORD = _phrase_pattern(INTERROGATIVES)
EXHAUSTIVE_PATTERN = _phrase_pattern(EXHAUSTIVE_CUES)
ROLE_PATTERN = _phrase_pattern(ROLE_CUES)

TIME_INTERROGATIVE = _phrase_pattern(("متى", "when"))
RANGE = re.compile(
    r"(?:^|\s)من\s+(?P<start>.+?)\s+(?:حتى|حتي|الى|إلى|لغايه|لغاية)\s+(?P<end>.+?)\s*[.؟?]*$"
    r"|(?:^|\s)from\s+(?P<start_en>.+?)\s+(?:to|until|till)\s+(?P<end_en>.+?)\s*[.?]*$"
)
AGGREGATION_PATTERN = _phrase_pattern(
    next(cues for intent, cues in INTENT_CUES if intent is Intent.AGGREGATION)
)

# Intents where an omitted or mis-attributed item is a factual error, so they are
# worth a structured extraction pass before the final answer.
HIGH_RISK_INTENTS = frozenset(
    {Intent.ENTITY, Intent.NUMERIC, Intent.DATE, Intent.COMPARISON,
     Intent.TIMELINE, Intent.AGGREGATION}
)
CONJUNCTION_SPLIT = re.compile(r"\s*[،؛,]\s*|\s+و(?=\s*(?:ما|كم|متى|من|هل|اين|لماذا|كيف)\b)")

#: The interrogatives as they appear after normalisation — "متى" is "متي" by then, so
#: comparing a normalised token against the list above never matched it.
NORMALISED_INTERROGATIVES = frozenset(normalize(w) for w in INTERROGATIVES) | {"ماذا", "اي"}
#: Wordings that ask for an overview of a file or matter rather than for a fact in it.
#: Such a question answered like any other lists everything the evidence holds — ten
#: sections, seventy lines, contact details included — when what was wanted is the
#: state of things in a paragraph.
#:
#: Requests, not nouns. "ملخص" and "summary" alone are left out: they are part of file
#: names ("PROJECT MIGRATION SUMMARY", "ملخص المدفوعات"), and "ما قيمة الدفعات في ملخص
#: المدفوعات؟" is a precise question. "اشرح" is left out too — "اشرح معادلة الغرامة"
#: wants the whole explanation, not a summary of it.
OVERVIEW_CUES = (
    "احكي", "احكيلي", "احكي لي", "لخص", "لخصلي", "اعطني ملخص", "اعطيني ملخص", "عايز ملخص",
    "ملخص عن", "نبذه عن", "معلومات عن", "كل شيء عن", "كل حاجه عن", "ايه اللي في",
    "ماذا يحتوي", "محتوي الملف", "عن الملف", "عن هذا الملف",
    "tell me about", "summarize", "summarise", "overview", "what is in",
    "information about", "information regarding", "everything about", "brief me",
)
OVERVIEW_PATTERN = _phrase_pattern(OVERVIEW_CUES)

#: Tokens that say nothing about the language a question is asked in: file names,
#: identifiers and codes. "احكيلي عن ملف PROJECT_MIGRATION_SUMMARY.md" has more Latin
#: letters than Arabic ones, and counting them answered an Arabic question in English.
_NOT_LANGUAGE = re.compile(r"\S*[_./\\@]\S*|(?<![\w؀-ۿ])[A-Z0-9][A-Z0-9-]{2,}(?![\w؀-ۿ])")


def language_of(question: str) -> str:
    """The language a question is asked in, ignoring the names and codes inside it."""
    stripped = _NOT_LANGUAGE.sub(" ", question)
    return detect_language(stripped) if stripped.strip() else detect_language(question)


def prose_letters(question: str) -> str:
    """The question without its names and codes — what language counting should read."""
    stripped = _NOT_LANGUAGE.sub(" ", question)
    return stripped if stripped.strip() else question


#: A token ending in one of these closes a part.
PART_BOUNDARY_AFTER = re.compile(r"[،؛,؟?]$")
#: "و" joined to these opens a new part even without a question word: "وكمان …".
PART_CONTINUATIONS = frozenset({"ايضا", "كمان", "برضه", "برضو"})


@dataclass(slots=True)
class QueryAnalysis:
    question: str
    normalized: str
    language: str
    intent: Intent
    keywords: list[str]
    identifiers: list[str]
    preferred_entity_kinds: tuple[str, ...]
    parts: list[str] = field(default_factory=list)
    multi_part: bool = False
    needs_multi_section: bool = False
    exhaustive: bool = False
    role_specific: bool = False
    high_risk: bool = False
    # "من بدء الحفر حتى الانهيار" — the two ends of an explicit span. Resolved to dates
    # later, against the evidence, so the answer covers that window in order.
    range_from: str = ""
    range_to: str = ""

    # What the question said about time, and whether it is about the document as an
    # object rather than about its subject. Both are read deterministically and both
    # carry the cue that fired, so any ranking they influence can be explained by
    # quoting the question rather than by asserting that a rule applied.
    temporal: TemporalIntent = field(default_factory=TemporalIntent)
    metadata_intent: EvidenceClass = field(default_factory=EvidenceClass)

    # The question in the words the documents use. `search_text` is the colloquial
    # forms mapped onto standard Arabic — identical to `normalized` for a question
    # already written that way. `expansions` are synonyms the keyword search adds at a
    # reduced weight, and `term_alternatives` lets ranking count a synonym as covering
    # the term it stands for. `question` itself is never rewritten: it is what the
    # model reads and answers.
    search_text: str = ""
    rewritten: bool = False
    #: The question as written with only its colloquial words replaced, correctly spelled.
    standard_question: str = ""
    expansions: tuple[str, ...] = ()
    term_alternatives: dict[str, frozenset[str]] = field(default_factory=dict)
    rewrite_notes: tuple[str, ...] = ()
    #: The parts in the asker's own wording, for the prompt. `parts` holds the same
    #: pieces in standard form, for analysis and validation.
    parts_display: list[str] = field(default_factory=list)
    #: Asks for an overview of a file or matter, not for a fact in it. Answered with
    #: the essentials and an offer of detail, never with everything the evidence holds.
    overview: bool = False

    @property
    def embedding_text(self) -> str:
        """What the semantic arm embeds: the standard form only when one was needed.

        Never the normalised `search_text`. Normalisation drops hamza and writes ة as ه,
        which is right for matching and wrong for a model trained on correctly written
        Arabic — "نتايج" is not a word it knows, and the search went to the wrong file.
        """
        return (self.standard_question or self.question) if self.rewritten else self.question

    @property
    def is_ranged(self) -> bool:
        return bool(self.range_from and self.range_to)

    @property
    def wants_latest(self) -> bool:
        """Asked for the most recent state of something.

        Kept separate from `Intent.TIMELINE` on purpose. A timeline question wants every
        dated event in order and is answered exhaustively; "what is the latest position?"
        wants the newest one and is answered briefly. Folding the second into the first
        made short questions produce long enumerations, which is a different wrong
        answer, not a fix.
        """
        return self.temporal.wants_latest

    @property
    def wants_earliest(self) -> bool:
        return self.temporal.wants_earliest

    @property
    def needs_dated_sweep(self) -> bool:
        """Whether retrieval should sweep the document's dated sections.

        A dated section states its date in its heading and says nothing that the wording
        of a "latest" question matches, so no lexical or semantic arm ever reaches it.
        The sweep is the only path to that evidence, and until this property existed it
        ran for `Intent.TIMELINE` alone — which is why a question phrased in the
        singular, "أحدث تحديث" rather than "آخر تحديثات", retrieved nothing dated.
        """
        return self.intent is Intent.TIMELINE or self.temporal.is_temporal

    @property
    def wants_wide_retrieval(self) -> bool:
        return self.needs_multi_section or self.multi_part or self.exhaustive


class QueryAnalyzer:
    def __init__(self, lexicon: SynonymLexicon | None = None) -> None:
        # Built-in groups only unless the container hands in one that also reads
        # approved terminology — so a test constructing this directly touches no database.
        self.lexicon = lexicon or SynonymLexicon()

    def analyze(self, question: str) -> QueryAnalysis:
        canonical = canonicalize(question)
        normalized = canonical.text
        intent = self._detect_intent(normalized)
        parts_display = self._split_parts(question)
        parts = (
            [canonicalize(p).text for p in parts_display]
            if len(parts_display) > 1 else [question.strip()]
        )
        multi_part = len(parts) > 1 or len(QUESTION_WORD.findall(normalized)) > 1
        expansion = self.lexicon.expand(normalized)

        keywords = [
            token for token in tokenize(normalized)
            if token not in NORMALISED_INTERROGATIVES
            and not (token.startswith("و") and token[1:] in NORMALISED_INTERROGATIVES)
            and len(token) > 1
        ]
        identifiers = IDENTIFIER.findall(normalized)

        # A timeline or aggregation question is a listing question by definition: the
        # answer is wrong if it names three of the four events the evidence supports.
        exhaustive = bool(EXHAUSTIVE_PATTERN.search(normalized)) or intent in (
            Intent.TIMELINE,
            Intent.AGGREGATION,
        )
        role_specific = bool(ROLE_PATTERN.search(normalized))
        range_from, range_to = self._detect_range(normalized)

        return QueryAnalysis(
            question=question.strip(),
            normalized=normalized,
            language=language_of(question),
            intent=intent,
            keywords=keywords,
            identifiers=identifiers,
            preferred_entity_kinds=KIND_PREFERENCE.get(intent, ("attribute",)),
            parts=parts,
            multi_part=multi_part,
            needs_multi_section=intent in WIDE_INTENTS or multi_part,
            exhaustive=exhaustive,
            role_specific=role_specific,
            high_risk=exhaustive or role_specific or multi_part or intent in HIGH_RISK_INTENTS,
            range_from=range_from,
            range_to=range_to,
            # Read from the original wording rather than the normalised form: both
            # helpers normalise internally, and `all_dates` needs the written-out month
            # names that tokenising would have broken up.
            temporal=read_intent(question),
            metadata_intent=query_wants_metadata(question),
            search_text=normalized,
            rewritten=canonical.changed,
            standard_question=canonical.display,
            expansions=tuple(expansion.terms),
            term_alternatives=expansion.alternatives,
            rewrite_notes=tuple(canonical.changes) + tuple(expansion.notes),
            parts_display=parts_display if len(parts_display) > 1 else [question.strip()],
            # An enumeration request ("اذكر كل البنود") is a listing question even when
            # it says "about the file": its answer is the list, not a summary of it.
            overview=bool(OVERVIEW_PATTERN.search(normalized)) and not exhaustive,
        )

    @staticmethod
    def _detect_range(normalized: str) -> tuple[str, str]:
        match = RANGE.search(normalized)
        if not match:
            return "", ""
        groups = match.groupdict()
        start = groups.get("start") or groups.get("start_en") or ""
        end = groups.get("end") or groups.get("end_en") or ""
        return start.strip(), end.strip()

    @staticmethod
    def _detect_intent(normalized: str) -> Intent:
        # "اذكر لي متى حصل …" asks when something happened. Cue order alone labels it
        # aggregation — because of "اذكر" — and it then gets no dated evidence, which is
        # how two separate events ended up reported under one date. When both a time
        # interrogative and an enumeration cue are present, the time reading wins.
        # A plain "متى …؟" stays a date question so it keeps its focused retrieval.
        if TIME_INTERROGATIVE.search(normalized) and AGGREGATION_PATTERN.search(normalized):
            return Intent.TIMELINE

        for intent, pattern in INTENT_PATTERNS:
            if pattern.search(normalized):
                return intent
        return Intent.FACT

    @staticmethod
    def _split_parts(question: str) -> list[str]:
        """The question's parts, in the asker's own words.

        Split on the written words rather than a normalised copy, so each part can be
        shown back exactly as it was typed. A part starts after a comma, a semicolon or
        a question mark, or at "و" joined to a question word — standard or colloquial:
        "وما", "ومتى", "وإمتى", "ومين", "وكمان".
        """
        tokens = question.split()
        groups: list[list[str]] = []
        current: list[str] = []
        for index, token in enumerate(tokens):
            starts = False
            if current:
                if PART_BOUNDARY_AFTER.search(tokens[index - 1]):
                    starts = True
                else:
                    word = normalize(token).strip(" ؟?.،,؛")
                    if word.startswith("و") and len(word) > 1:
                        rest = word[1:]
                        starts = (
                            canonicalize(rest).text in NORMALISED_INTERROGATIVES
                            or rest in PART_CONTINUATIONS
                        )
            if starts:
                groups.append(current)
                current = []
            current.append(token)
        if current:
            groups.append(current)
        pieces = [" ".join(g).strip(" ؟?.،,؛") for g in groups]
        meaningful = [p for p in pieces if len(p) > 6]
        return meaningful if len(meaningful) > 1 else [question.strip()]
