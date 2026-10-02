"""Finding disagreements between sources, and refusing to settle the ones metadata cannot.

Three kinds are detected with one mechanism: document against document, taught claim
against taught claim, and taught claim against document. Nothing here knows what any
particular question or file is about — a conflict is two sources that talk about the same
thing and state different values for it, and "the same thing" is decided by shared
wording, not by a list of concepts someone maintained by hand.

When authority metadata names a winner the decision is recorded with its reason. When it
does not, the disagreement itself is the output. That is the whole contract: the system
would rather hand back two numbers with their sources than quietly pick the wrong one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.authority import AuthorityDecision, Basis, SourceAuthority, resolve
from app.core.knowledge import SCOPE_RANK, KnowledgeScope
from app.core.text import (
    DATE_LIKE,
    IDENTIFIER,
    STOPWORDS,
    content_terms,
    normalize,
    stem,
    strip_thousands,
)
from app.core.number_words import with_digits
from app.services.knowledge_service import ActiveKnowledge

logger = logging.getLogger(__name__)

#: How many meaningful words two *labels* must share before the values beside them are
#: treated as claims about the same thing. Two is the smallest value that still requires
#: a subject and a qualifier to match — "الرقم 12" against "الرقم 99" shares one word and
#: is not a disagreement about anything.
MIN_SHARED_TERMS = 2
#: Numbers shorter than this are ordinals, list markers and clause numbers far more often
#: than they are values worth disputing.
MIN_VALUE_LENGTH = 2
MAX_CONFLICTS = 12

#: A number with no words in front of it states nothing that can be contradicted, and a
#: number with one word in front of it ("الرقم 12") states too little to contradict.
MIN_LABEL_TERMS = 2
#: How far back to look for the words that name what a number measures. A label is a
#: noun phrase, not a paragraph; reading further back starts borrowing the words of the
#: previous sentence and inventing attributes that were never stated.
LABEL_WINDOW = 8

#: Years standing alone are context, not quantities. Used only when the number carries
#: no unit — "2026 درهم" is a sum of money that happens to look like a year.
YEAR_RANGE = range(1900, 2101)

#: Numbers as they are written, including thousands separators and a trailing percent.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
#: A cell or clause boundary. A label belongs to the number in its own cell, so a
#: markdown table row does not lend the first column's words to the last column's value.
SEGMENT = re.compile(r"[|\n\r;؛]+")
#: Dates written out in words, which `DATE_LIKE` does not cover.
WRITTEN_DATE_NUMBERS = re.compile(
    r"\b\d{1,2}\s+[^\W\d_]+\s+\d{4}\b"
)
#: A number opening a line or a cell and followed by a separator is a list marker or a
#: section number — "11. الملفات المنتجة", "6-ع", "3)" — not a measured value.
SECTION_MARKER = re.compile(r"^\s*\d+\s*[.)\-–—/]")


@dataclass(slots=True)
class ConflictSide:
    """One party to a disagreement, with everything needed to cite it."""

    ref: str
    kind: str                # document | knowledge
    values: list[str]
    label: str = ""
    citation: int = 0
    item_id: str = ""
    version_id: str = ""
    excerpt: str = ""

    def describe(self) -> str:
        where = f"[{self.citation}]" if self.citation else self.label or self.ref
        return f"{', '.join(self.values)} ({where})"


@dataclass(slots=True)
class _Party:
    """One source, with everything it measures. Internal to the detector.

    Separate from `ConflictSide` because a source is not a party to one disagreement —
    it may state a dozen quantities, and only the one that actually clashes belongs in
    the report. Reporting the whole set, as the previous version did, is what produced
    lines listing fourteen unrelated numbers against each other.
    """

    ref: str
    kind: str
    excerpt: str
    measurements: list[Measurement]
    label: str = ""
    citation: int = 0
    item_id: str = ""
    version_id: str = ""

    def side(self, values: list[str]) -> ConflictSide:
        return ConflictSide(
            ref=self.ref,
            kind=self.kind,
            values=values,
            label=self.label,
            citation=self.citation,
            item_id=self.item_id,
            version_id=self.version_id,
            excerpt=self.excerpt,
        )


@dataclass(slots=True)
class Conflict:
    """Two sources that disagree, and what — if anything — settled it."""

    left: ConflictSide
    right: ConflictSide
    shared_terms: list[str] = field(default_factory=list)
    decision: AuthorityDecision | None = None

    @property
    def resolved(self) -> bool:
        return self.decision is not None and self.decision.resolved

    @property
    def winner_ref(self) -> str:
        return self.decision.winner.ref if self.resolved and self.decision.winner else ""

    def describe(self) -> str:
        """One line for the answer's validation notes, in the reader's language."""
        subject = "، ".join(self.shared_terms[:3]) or "القيمة"
        head = f"تعارض حول «{subject}»: {self.left.describe()} مقابل {self.right.describe()}"
        if self.resolved and self.decision:
            return f"{head} — رُجّح: {self.decision.explanation}"
        return f"{head} — لا توجد بيانات تُرجّح أحدهما، فاعرضهما معًا بمصدريهما."


@dataclass(frozen=True, slots=True)
class Measurement:
    """One stated quantity, together with what the passage says it measures.

    This is the unit of comparison, and replacing a bare set of numbers with it is the
    whole of the fix. The old version collected every digit run in a passage and called
    two passages contradictory whenever they shared a couple of common words and did not
    share a number. In a working file full of tables that describes almost any two
    passages: section numbers, reference codes, revision numbers and dates all counted as
    values, and "ملف" and "كامل" counted as agreement on a subject. The detector filled
    its twelve-conflict ceiling with noise on every question asked of it, including
    questions about entirely different documents.

    A conflict now needs the same *attribute* on both sides — the words naming what was
    measured — the same kind of unit, and different values. Anything less is recorded as
    nothing at all, which is the correct output for two numbers that were never claims
    about the same thing.
    """

    value: str
    attribute: frozenset[str]
    unit: str = ""
    raw: str = ""

    def agrees_with(self, other: Measurement) -> bool:
        return self.value == other.value

    def about_the_same_thing(self, other: Measurement) -> bool:
        """Whether both numbers were offered as values for one attribute."""
        if len(self.attribute & other.attribute) < MIN_SHARED_TERMS:
            return False
        return _units_compatible(self.unit, other.unit)


def _units_compatible(left: str, right: str) -> bool:
    """Two quantities can only disagree if they are quantities of the same kind.

    An amount in dirhams and a duration in days are not rival answers however similarly
    they are introduced. One side omitting the unit is tolerated: documents state it
    once in a heading and leave it out of the rows beneath.
    """
    if not left or not right:
        return True
    if left == right:
        return True
    a, b = stem(left), stem(right)
    return a == b or a.startswith(b) or b.startswith(a)


def _masked(text: str) -> str:
    """The passage with everything that is not a quantity blanked out.

    Dates, identifiers, version strings and reference codes are removed *before* numbers
    are read, rather than filtered afterwards, because their digits are what made them
    look like values: `21/06/2026` offered `21`, `06` and `2026`, and `fb/2026-147`
    offered `2026` and `147`. Blanking preserves the character positions, so the words
    around a real number still line up with it.
    """
    masked = text
    for pattern in (WRITTEN_DATE_NUMBERS, DATE_LIKE, IDENTIFIER):
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return masked


def _is_quantity(raw: str, value: str, unit: str, segment: str, start: int) -> bool:
    """Whether a number was offered as a measured amount at all."""
    # Short numbers are ordinals and clause markers far more often than values — unless
    # something states what they measure. "5%" and "3 أيام" are quantities; a bare "5"
    # beside two words is as likely to be the fifth item in a list.
    if not unit and len(value.replace(".", "")) < MIN_VALUE_LENGTH:
        return False
    # A bare year is context — "قرار 2026" names a year, not a sum. With a unit beside
    # it, it is a quantity that happens to look like one.
    if not unit and value.isdigit() and int(value) in YEAR_RANGE and "," not in raw:
        return False
    if SECTION_MARKER.match(segment[: start + len(raw) + 1]):
        return False
    return True


def _measurements(text: str) -> list[Measurement]:
    """Every quantity the passage states, with the words that name it.

    Read per segment — a line, or a single cell of a table row — so the first column of
    a row never lends its words to the last column's number.
    """
    normalised = normalize(text)
    found: list[Measurement] = []

    for segment in SEGMENT.split(normalised):
        if not segment.strip():
            continue
        # "مهلة الإخطار ثلاثون يومًا" states a quantity exactly as "… 30 يومًا" does,
        # and a disagreement between the two is as real as one between two digits.
        segment = with_digits(segment)
        masked = _masked(segment)
        for match in NUMBER.finditer(masked):
            raw = match.group(0)
            value = strip_thousands(raw).rstrip("%").strip(".,:")
            before = segment[: match.start()]
            after = segment[match.end():]

            unit = "%" if raw.endswith("%") else _leading_word(after)
            if not _is_quantity(raw, value, unit, segment, match.start()):
                continue

            label = _label_terms(before)
            if len(label) < MIN_LABEL_TERMS:
                continue  # nothing names what this number measures

            found.append(
                Measurement(value=value, attribute=frozenset(label), unit=unit, raw=raw)
            )
    return found


def _leading_word(text: str) -> str:
    """The word immediately after a number, which is usually its unit.

    A stopword is not a unit. "البند 6 من العقد" is followed by "من", and treating that
    as a unit would turn every clause number into a measured quantity — the exact class
    of noise this rewrite exists to remove.
    """
    stripped = text.lstrip(" \t.,:")
    word = ""
    for char in stripped:
        if char.isalpha():
            word += char
        else:
            break
    if not (1 < len(word) <= 12) or word in STOPWORDS:
        return ""
    return word


def _label_terms(before: str) -> set[str]:
    """The words naming what the number that follows them measures."""
    words = before.split()[-LABEL_WINDOW:]
    return _terms(" ".join(words))


def _terms(text: str) -> set[str]:
    return {t for t in content_terms(text) if len(t) > 2 and not t.isdigit()}


class ConflictDetector:
    """Detects disagreements and applies the authority rules to them."""

    def detect(
        self,
        sources: list,
        knowledge: list[ActiveKnowledge],
        *,
        knowledge_authority: dict[str, SourceAuthority] | None = None,
        document_authority: dict[str, SourceAuthority] | None = None,
    ) -> list[Conflict]:
        """Every disagreement across both kinds of source, resolved where metadata allows."""
        doc_sides = self._document_sides(sources)
        knowledge_sides = self._knowledge_sides(knowledge)

        conflicts: list[Conflict] = []
        conflicts += self._pairwise(doc_sides, doc_sides, same_pool=True)
        conflicts += self._pairwise(knowledge_sides, knowledge_sides, same_pool=True)
        conflicts += self._pairwise(knowledge_sides, doc_sides, same_pool=False)

        authority = {**(document_authority or {}), **(knowledge_authority or {})}
        for conflict in conflicts[:MAX_CONFLICTS]:
            left = authority.get(conflict.left.ref) or self._fallback_authority(conflict.left)
            right = authority.get(conflict.right.ref) or self._fallback_authority(conflict.right)
            conflict.decision = resolve(left, right)

        if conflicts:
            unresolved = sum(1 for c in conflicts[:MAX_CONFLICTS] if not c.resolved)
            logger.info(
                "Conflicts: %s detected, %s left unresolved and shown to the reader",
                len(conflicts[:MAX_CONFLICTS]), unresolved,
            )
        return conflicts[:MAX_CONFLICTS]

    # -- building the sides -----------------------------------------------
    @staticmethod
    def _document_sides(sources: list) -> list[_Party]:
        parties: list[_Party] = []
        for source in sources:
            excerpt = getattr(source, "excerpt", "") or ""
            measurements = _measurements(excerpt)
            if not measurements:
                continue
            citation = int(getattr(source, "citation", 0) or 0)
            parties.append(
                _Party(
                    ref=f"doc:{citation}",
                    kind="document",
                    label=getattr(source, "filename", "") or "",
                    citation=citation,
                    excerpt=excerpt,
                    measurements=measurements,
                )
            )
        return parties

    @staticmethod
    def _knowledge_sides(knowledge: list[ActiveKnowledge]) -> list[_Party]:
        parties: list[_Party] = []
        for item in knowledge:
            if not item.is_factual:
                continue  # a rule or a preference asserts nothing to disagree about
            measurements = _measurements(item.content)
            if not measurements:
                continue
            parties.append(
                _Party(
                    ref=f"knowledge:{item.item_id}",
                    kind="knowledge",
                    label=item.source_text or "معرفة معتمدة",
                    item_id=item.item_id,
                    version_id=item.version_id,
                    excerpt=item.content,
                    measurements=measurements,
                )
            )
        return parties

    def _pairwise(
        self, left_pool: list[_Party], right_pool: list[_Party], *, same_pool: bool
    ) -> list[Conflict]:
        """Disagreements between two pools, one attribute at a time.

        The comparison is between *measurements*, not between passages. Two passages no
        longer conflict because they happen to share two words and differ somewhere in
        their digits; a specific number introduced by a specific noun phrase conflicts
        with another number introduced by the same noun phrase, in the same kind of
        unit, and nothing else does.
        """
        conflicts: list[Conflict] = []
        seen: set[tuple[str, str, frozenset[str]]] = set()

        for i, left in enumerate(left_pool):
            start = i + 1 if same_pool else 0
            for right in right_pool[start:]:
                if left.ref == right.ref:
                    continue

                for lm in left.measurements:
                    for rm in right.measurements:
                        if not lm.about_the_same_thing(rm):
                            continue
                        if lm.agrees_with(rm):
                            continue

                        attribute = lm.attribute & rm.attribute
                        key = (*sorted((left.ref, right.ref)), attribute)
                        if key in seen:
                            continue
                        seen.add(key)

                        conflicts.append(
                            Conflict(
                                left=left.side([lm.value]),
                                right=right.side([rm.value]),
                                shared_terms=sorted(attribute)[:5],
                            )
                        )
        return conflicts

    @staticmethod
    def _fallback_authority(side: ConflictSide) -> SourceAuthority:
        """Used when a side carries no declared metadata — so it settles nothing."""
        return SourceAuthority(
            ref=side.ref,
            kind=side.kind,
            label=side.label,
            scope_rank=SCOPE_RANK.get(KnowledgeScope.GLOBAL, 3) if side.kind == "knowledge" else -1,
        )


def document_authorities(sources: list, metadata: dict[str, dict]) -> dict[str, SourceAuthority]:
    """Builds the authority record for each cited passage from its document's metadata."""
    out: dict[str, SourceAuthority] = {}
    for source in sources:
        citation = int(getattr(source, "citation", 0) or 0)
        document_id = getattr(source, "document_id", "") or ""
        meta = metadata.get(document_id, {})
        out[f"doc:{citation}"] = SourceAuthority(
            ref=f"doc:{citation}",
            kind="document",
            status=str(meta.get("doc_status", "") or ""),
            authority=int(meta.get("authority", 0) or 0),
            effective_date=meta.get("effective_date") or None,
            version=str(getattr(source, "version", "") or meta.get("version", "") or ""),
            label=getattr(source, "filename", "") or "",
        )
    return out


def knowledge_authorities(items: list[ActiveKnowledge]) -> dict[str, SourceAuthority]:
    return {
        f"knowledge:{item.item_id}": SourceAuthority(
            ref=f"knowledge:{item.item_id}",
            kind="knowledge",
            status="active",
            scope_rank=SCOPE_RANK.get(item.scope, 0),
            version=str(item.version_no),
            label=item.source_text or "معرفة معتمدة",
        )
        for item in items
    }
