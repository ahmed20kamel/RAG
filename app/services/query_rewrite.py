"""Reading a question in the words the documents use.

Two gaps between how people ask and how files are written.

**Dialect.** "كام بندفع عن كل يوم تأخير؟", "إمتى اتمضى العقد؟", "مين المهندس؟". The
question words the analyser knows are Modern Standard Arabic, so "كام" is not read as
"how much" and the question is not treated as a numeric one; worse, "كام" and "بندفع"
enter the keyword search as content words and match nothing. `canonicalize` maps a
curated list of colloquial forms onto their standard equivalents. The mapped text is
used for analysis and search only — the model still reads the question exactly as it
was asked, and answers in kind.

**Synonyms.** A question about "الجدار" and a file about "السور". The semantic arm often
bridges this; the keyword arm cannot, and neither can the ranking features that count
shared terms. `SynonymLexicon` adds the other members of a synonym group to the keyword
search, at a reduced weight so a synonym can never outrank the word actually asked, and
lets the ranking count a synonym as covering the term it stands for.

The built-in groups are general Arabic contract and construction vocabulary. Anything
specific to one organisation or one matter is *learned*: an approved terminology item
("«اختبار» في الأسئلة يُقصد به «معاينة» في المستندات") becomes a group of its own. Only
globally scoped, active items are used, because expansion changes what everybody's
search returns and a personal note must not do that.

Deliberately conservative. A dialect form is mapped only when it is unambiguous as a
whole word — "الي" is not touched because it is also the normalised "إلى", and the
Egyptian "ب" prefix is expanded only for a listed set of verbs, because stripping it
generally would turn "بنود" into "نود".
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.core.text import normalize, tokenize

logger = logging.getLogger(__name__)

_L = r"(?<![\w؀-ۿ])"
_R = r"(?![\w؀-ۿ])"

# ---------------------------------------------------------------------------
# Dialect → standard, on normalised spelling (alef folded, ى→ي, ة→ه)
# ---------------------------------------------------------------------------

#: Multi-word forms first: they would otherwise be broken by the single-word rules.
_PHRASES: dict[str, str] = {
    "قد ايه": "كم",
    "اد ايه": "كم",
    "كام يوم": "كم يوما",
    "اول من امبارح": "اول امس",
}

_WORDS: dict[str, str] = {
    # question words
    "كام": "كم", "بكام": "بكم",
    "امتي": "متي", "امتا": "متي",
    "مين": "من",
    "ايه": "ما", "ايش": "ما", "شو": "ما", "ايشو": "ما",
    "فين": "اين", "وين": "اين",
    "ازاي": "كيف", "كيفاش": "كيف",
    "ليه": "لماذا", "ليش": "لماذا",
    "انهي": "اي", "انهو": "اي",
    # time
    "دلوقتي": "الان", "دلوقت": "الان", "الحين": "الان", "هلق": "الان",
    "امبارح": "امس", "بكره": "غدا", "بكرا": "غدا",
    # common function words
    "عايز": "اريد", "عاوز": "اريد", "عايزه": "اريد", "عاوزه": "اريد",
    "ابغي": "اريد", "ابغا": "اريد", "بدي": "اريد",
    "اللي": "الذي",
    "كمان": "ايضا", "برضه": "ايضا", "برضو": "ايضا",
    "مش": "لا", "مو": "لا",
    "شغل": "اعمال", "الشغل": "الاعمال",
    "فلوس": "مبالغ", "الفلوس": "المبالغ",
    "حاجه": "شيء",
    # passive forms common in questions about events
    "اتمضي": "وقع", "اتمضت": "وقعت",
    "اتعمل": "تم", "اتعملت": "تمت",
    "اتسلم": "استلم", "اتسلمت": "استلمت",
    "اتقدم": "قدم", "اتقدمت": "قدمت",
    "اترفع": "رفع", "اترفعت": "رفعت",
    "اتدفع": "دفع", "اتدفعت": "دفعت",
    "اتبني": "بني", "اتبنت": "بنيت",
    "اتحكم": "حكم", "اتصدر": "صدر", "اتصدرت": "صدرت",
    "اتهد": "انهار",
    # "to become / to be" in the future, as questions about amounts put it
    "هتبقي": "ستكون", "هيبقي": "سيكون", "هتكون": "ستكون", "هيكون": "سيكون",
    "بتبقي": "تكون", "بيبقي": "يكون",
}

#: Words dropped from the search text: fillers that carry no meaning to match.
#: Demonstratives are here rather than mapped: "دي" is also the end of "ودي" (amicable),
#: and a whole-word filler never touches the inside of another word.
_FILLERS = {"بتاع", "بتاعه", "بتاعت", "بتوع", "يعني", "بقي", "كده", "كدا", "طيب", "يا",
            "ده", "دا", "دي"}

#: Verbs whose Egyptian progressive ("ب") and future ("ه") forms are expanded. Listed,
#: not inferred: the prefix letters begin far too many ordinary nouns.
_VERBS = ("يدفع", "يحدد", "يبدا", "ينتهي", "يحسب", "يسدد", "يقدم", "يصدر", "يطلب",
          "يحكم", "يستحق", "يبلغ", "يكون", "يتم", "يلزم", "يحق", "يغطي", "يشمل", "يتحمل",
          "يعمل", "يسلم", "يستلم", "يرفع", "يوقع")
for _verb in _VERBS:
    _stem = _verb[1:]
    for _person in ("ي", "ن", "ت"):
        _WORDS.setdefault("ب" + _person + _stem, _person + _stem)
        _WORDS.setdefault("ه" + _person + _stem, "س" + _person + _stem)


def _phrase_pattern(words: Iterable[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(f"{_L}(?:{alternatives}){_R}")


_PHRASE_RE = _phrase_pattern(_PHRASES)
_WORD_RE = re.compile(rf"{_L}(و?)({'|'.join(re.escape(w) for w in sorted(_WORDS, key=len, reverse=True))}){_R}")
_FILLER_RE = _phrase_pattern(_FILLERS)


#: Standard words as they are spelled, for text a model reads. The mapping above works on
#: normalised spelling — no hamza, ة as ه — which is right for matching and wrong for an
#: embedding model trained on correctly written Arabic: "نتايج" is not a word it knows.
_PROPER: dict[str, str] = {
    "متي": "متى", "ومتي": "ومتى", "اين": "أين", "اريد": "أريد", "الان": "الآن", "امس": "أمس",
    "غدا": "غدًا", "اي": "أي", "ايضا": "أيضًا", "اعمال": "أعمال", "الاعمال": "الأعمال",
    "اول امس": "أول أمس", "كم يوما": "كم يومًا",
    "وقع": "وُقّع", "وقعت": "وُقّعت", "استلم": "استُلم", "استلمت": "استُلمت",
    "قدم": "قُدّم", "قدمت": "قُدّمت", "رفع": "رُفع", "رفعت": "رُفعت", "دفع": "دُفع",
    "دفعت": "دُفعت", "بني": "بُني", "بنيت": "بُنيت", "حكم": "حُكم",
}


def _proper(standard: str) -> str:
    if standard in _PROPER:
        return _PROPER[standard]
    # The generated forms of "يبدأ" carry its folded hamza: "نبدا", "سيبدا".
    return standard[:-3] + "بدأ" if standard.endswith("بدا") and len(standard) > 3 else standard


@dataclass(frozen=True)
class Canonical:
    """A question as the analyser should read it, and what was changed."""

    text: str
    changes: tuple[str, ...] = ()
    #: The question as written, with only the colloquial words replaced — correctly
    #: spelled standard Arabic, for the embedding model. Equal to the question when
    #: nothing was changed.
    display: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def canonicalize(question: str) -> Canonical:
    """Map colloquial forms onto standard Arabic, for analysis and search only."""
    normalised = normalize(question)
    changes: list[str] = []

    def phrase(match: re.Match) -> str:
        changes.append(f"{match.group(0)}→{_PHRASES[match.group(0)]}")
        return _PHRASES[match.group(0)]

    def word(match: re.Match) -> str:
        conjunction, form = match.group(1), match.group(2)
        standard = _WORDS[form]
        changes.append(f"{form}→{standard}")
        return f"{conjunction}{standard}"

    text = _PHRASE_RE.sub(phrase, normalised)
    text = _WORD_RE.sub(word, text)
    if not changes:
        return Canonical(text=normalised, display=question.strip())
    stripped = _FILLER_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", stripped).strip()
    return Canonical(text=text, changes=tuple(changes), display=_standardise_written(question))


_TOKEN_EDGES = re.compile(r"^([^\w؀-ۿ]*)(.*?)([^\w؀-ۿ]*)$", re.DOTALL)


def _standardise_written(question: str) -> str:
    """The question with its colloquial words replaced, everything else as written."""
    tokens = question.split()
    out: list[str] = []
    index = 0
    while index < len(tokens):
        lead, core, trail = _TOKEN_EDGES.match(tokens[index]).groups()
        if index + 1 < len(tokens):
            _, next_core, next_trail = _TOKEN_EDGES.match(tokens[index + 1]).groups()
            pair = normalize(f"{core} {next_core}")
            if pair in _PHRASES:
                out.append(f"{lead}{_proper(_PHRASES[pair])}{next_trail}")
                index += 2
                continue
        folded = normalize(core)
        conjunction, bare = ("و", folded[1:]) if folded.startswith("و") and folded[1:] in _WORDS else ("", folded)
        if bare in _WORDS and folded not in _FILLERS:
            out.append(f"{lead}{conjunction}{_proper(_WORDS[bare])}{trail}")
        elif folded in _FILLERS:
            if trail.strip():
                out.append(trail.strip())
        else:
            out.append(tokens[index])
        index += 1
    return " ".join(part for part in out if part)


# ---------------------------------------------------------------------------
# Synonyms
# ---------------------------------------------------------------------------

#: General contract and construction vocabulary. Each group is a set of words a
#: document may use for one thing. Nothing here names a party, a matter or a place.
BUILT_IN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("عقد", "تعاقد", "اتفاقيه"),
    ("سور", "جدار", "حائط"),
    ("غرامه", "جزاء"),
    ("مبلغ", "قيمه"),
    ("ثمن", "سعر", "تكلفه"),
    ("مقاول", "متعهد"),
    ("مالك", "صاحب"),
    ("موعد", "تاريخ"),
    ("مده", "فتره", "مهله"),
    ("توقيع", "امضاء"),
    ("بدء", "بدايه", "شروع"),
    ("انتهاء", "نهايه"),
    ("انهيار", "سقوط", "انهار", "سقط"),
    ("رخصه", "تصريح"),
    ("شكوي", "بلاغ"),
    # Not ("دعوي", "قضيه"): in a legal file both words are on nearly every page, so
    # expanding one into the other reaches nothing new and only reorders the evidence of
    # any question that merely says "في هذه القضية". A synonym earns its place by
    # reaching passages the asked word misses; words that are everywhere cannot.
    ("رسوم", "اتعاب"),
    ("ضمان", "كفاله"),
    ("حفر", "حفريات"),
    ("تسليم", "استلام"),
    ("اخطار", "اشعار", "تبليغ"),
    ("خساره", "ضرر"),
    ("انجاز", "تنفيذ"),
    ("نسبه", "معدل"),
    ("محامي", "وكيل"),
    ("موظف", "عامل"),
    ("اجازه", "عطله"),
)

#: How much a synonym counts in the keyword search, against 1.0 for a word asked.
EXPANSION_WEIGHT = 0.5

#: A learned term is used only when both sides are short enough to be terms.
MAX_TERM_WORDS = 5

_QUOTED = re.compile(r"«([^»]{2,60})»")
_MEANS = re.compile(
    r"^\s*(?:يقصد|المقصود|نعني|يعني)\s*ب(?:ال)?\s*(?P<a>[\w؀-ۿ]+)"
    r"(?:\s+(?:في|عندنا|لدينا|بالنسبه لنا)\s+[\w؀-ۿ]+)?\s+(?P<b>.+?)(?:\s+(?:وليس|لا|وليست)\s.*)?[.،]?$"
)
#: "هو" and "هي" are left out: "المقاول هو المسؤول عن التنفيذ" is a definition, not a synonym.
_EQUALS = re.compile(r"^\s*(?P<a>[^=:]{2,60}?)\s*(?:=|:|يعني|تعني)\s*(?P<b>[^=:]{2,60})\s*$")


def parse_term(content: str) -> tuple[str, str] | None:
    """The two sides of a terminology statement, or None if it is not one.

    Accepts the forms people actually write: two quoted terms, "يقصد بـX Y", or
    "X = Y" / "X يعني Y". Anything longer than a few words a side is a definition,
    not a synonym, and expanding a search with a sentence would match everything.
    """
    quoted = _QUOTED.findall(content)
    if len(quoted) == 2:
        pair = (normalize(quoted[0]), normalize(quoted[1]))
    else:
        normalised = normalize(content)
        match = _MEANS.match(normalised) or _EQUALS.match(normalised)
        if not match:
            return None
        pair = (match.group("a").strip(), match.group("b").strip())
    a, b = pair
    if not a or not b or a == b:
        return None
    if len(a.split()) > MAX_TERM_WORDS or len(b.split()) > MAX_TERM_WORDS:
        return None
    return a, b


@dataclass
class Expansion:
    """What a question gains from the lexicon."""

    terms: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: stem → stems that count as the same term when ranking
    alternatives: dict[str, frozenset[str]] = field(default_factory=dict)


class SynonymLexicon:
    """Built-in groups plus learned terms, refreshed from the knowledge base."""

    def __init__(
        self,
        groups: Iterable[Iterable[str]] = BUILT_IN_GROUPS,
        learned: Callable[[], list[tuple[str, str]]] | None = None,
        refresh_seconds: float = 60.0,
    ) -> None:
        self._built_in = [tuple(normalize(w) for w in g) for g in groups]
        self._learned_loader = learned
        self._refresh_seconds = refresh_seconds
        self._learned: list[tuple[str, ...]] = []
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _groups(self) -> list[tuple[str, ...]]:
        if self._learned_loader is not None and time.monotonic() - self._loaded_at > self._refresh_seconds:
            with self._lock:
                if time.monotonic() - self._loaded_at > self._refresh_seconds:
                    try:
                        self._learned = [tuple(p) for p in self._learned_loader()]
                    except Exception:  # noqa: BLE001
                        # A lexicon that cannot load its learned half still has its
                        # built-in half; search must never fail on this.
                        logger.exception("Could not load learned terminology")
                    self._loaded_at = time.monotonic()
        return self._built_in + self._learned

    @property
    def learned_count(self) -> int:
        self._groups()
        return len(self._learned)

    def expand(self, text: str, extra: list[tuple[str, ...]] | None = None) -> Expansion:
        """The other members of every group the text touches.

        `extra` adds one reader's own terms — learned from their rephrasings or taught by
        them — for this question only; they never enter anyone else's search.
        """
        stems = set(tokenize(text))
        result = Expansion()
        alternatives: dict[str, set[str]] = {}
        personal = [tuple(normalize(w) for w in group) for group in (extra or [])]
        for group in self._groups() + personal:
            member_stems = [tuple(tokenize(member)) for member in group]
            present = [
                i for i, ms in enumerate(member_stems) if ms and all(s in stems for s in ms)
            ]
            if not present:
                continue
            others = [group[i] for i in range(len(group)) if i not in present]
            for other in others:
                if other not in result.terms:
                    result.terms.append(other)
            result.notes.append(f"{'/'.join(group[i] for i in present)} ≈ {'/'.join(others)}")
            singles = [ms[0] for ms in member_stems if len(ms) == 1]
            for stem in singles:
                alternatives.setdefault(stem, set()).update(s for s in singles if s != stem)
        result.alternatives = {k: frozenset(v) for k, v in alternatives.items() if v}
        return result


def learned_terms_from_db() -> list[tuple[str, str]]:
    """Approved, globally scoped terminology, as (asked, meant) pairs."""
    from sqlalchemy import select

    from app.core.knowledge import KnowledgeScope, KnowledgeStatus, KnowledgeType
    from app.models.database import session_scope
    from app.models.knowledge_items import KnowledgeItem, KnowledgeVersion

    pairs: list[tuple[str, str]] = []
    with session_scope() as db:
        rows = db.execute(
            select(KnowledgeVersion.content)
            .join(KnowledgeItem, KnowledgeItem.active_version_id == KnowledgeVersion.id)
            .where(
                KnowledgeItem.type == KnowledgeType.TERMINOLOGY,
                KnowledgeItem.status == KnowledgeStatus.ACTIVE,
                KnowledgeItem.scope == KnowledgeScope.GLOBAL,
            )
        ).all()
    for (content,) in rows:
        pair = parse_term(content or "")
        if pair is not None:
            pairs.append(pair)
    return pairs


# ---------------------------------------------------------------------------
# Learning from a clarification
# ---------------------------------------------------------------------------

#: Read on the raw answer: normalising removes the Arabic comma this anchors on.
_MEANT = re.compile(r"(?:إن|ان|إذا|اذا)\s+كنت\s+تقصد\s+(?P<meant>[^،,.\n]{2,80}?)\s*[،,]")


def interpretation_offered(answer: str) -> str | None:
    """The file's wording the clarification pass offered, if it offered one."""
    match = _MEANT.search(answer)
    if not match:
        return None
    meant = normalize(match.group("meant").strip(" «»\"'"))
    return meant if 0 < len(meant.split()) <= MAX_TERM_WORDS else None


def personal_terms(user_id: str | None) -> list[tuple[str, str]]:
    """One reader's own active terminology, as (asked, meant) pairs."""
    if not user_id:
        return []
    from sqlalchemy import select

    from app.core.knowledge import KnowledgeScope, KnowledgeStatus, KnowledgeType
    from app.models.database import session_scope
    from app.models.knowledge_items import KnowledgeItem, KnowledgeVersion

    pairs: list[tuple[str, str]] = []
    with session_scope() as db:
        rows = db.execute(
            select(KnowledgeVersion.content)
            .join(KnowledgeItem, KnowledgeItem.active_version_id == KnowledgeVersion.id)
            .where(
                KnowledgeItem.type == KnowledgeType.TERMINOLOGY,
                KnowledgeItem.status == KnowledgeStatus.ACTIVE,
                KnowledgeItem.scope == KnowledgeScope.USER,
                KnowledgeItem.owner_user_id == user_id,
            )
        ).all()
    for (content,) in rows:
        pair = parse_term(content or "")
        if pair is not None:
            pairs.append(pair)
    return pairs


def term_statement(asked: str, meant: str) -> str:
    """The wording a learned term is recorded in — the form `parse_term` reads back."""
    return f"«{asked}» في الأسئلة يُقصد به «{meant}» في المستندات"
