"""Structured evidence: the label/value facts behind the retrieved sections.

The problem this module solves: asked to list the parties to a matter, a model given
only prose may summarise some of them and attach one party's role to another. The
document itself usually states the mapping plainly in a table — "الممثل القانوني =
<اسم>", "المالك = <اسم>" — and that mapping is extracted at ingestion. Putting it in
front of the model as label/value pairs removes the need to infer it from prose.

Everything here is a verbatim label/value pair from the document, selected by rule.
No model is involved, so no fact can be invented and no two holders can be merged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.core.retrieval import EvidenceTier
from app.core.text import content_terms, extract_dates, normalize, parse_date
from app.models.knowledge import EntityRecord
from app.schemas.chat import SourceFact, SourceReference
from app.services.query_analysis import Intent, QueryAnalysis


@dataclass(frozen=True, slots=True)
class SectionSlot:
    """A section's place in the citation list."""

    citation: int
    heading: str
    primary: bool

logger = logging.getLogger(__name__)

# Kinds that carry a holder or a value worth stating explicitly. "attribute" covers the
# label/value table rows, which are the most precise facts in these documents.
ROLE_KINDS = ("attribute", "person", "organization")
VALUE_KINDS = ("amount", "percentage", "duration", "date", "identifier", "clause", "form")

HEADER = (
    "الوقائع المستخرجة حرفيًا من المصادر — كل سطر مأخوذ كما هو من المستند مع رقم مصدره.\n"
    "التزم بها كما هي، ولا تنسب دور أحدهم إلى آخر، ولا تُسقط أي سطر يخص السؤال:"
)

TIMELINE_HEADER = (
    "الجدول الزمني المستخرج من عناوين الأقسام — من الأحدث إلى الأقدم.\n"
    "اذكر كل سطر منه في إجابتك بتاريخه ورقم مصدره، ولا تُسقط أيًّا منها:"
)

DECORATION = re.compile(
    r"[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]+"
)
RANGE_HEADER = (
    "الأحداث الواقعة داخل النطاق الذي حدده السؤال — مرتبة من الأقدم إلى الأحدث.\n"
    "اذكرها كلها بهذا الترتيب مع تاريخ ورقم مصدر كل حدث، ولا تُدخل حدثًا خارج النطاق:"
)

MAX_TIMELINE_ENTRIES = 16
MAX_TIMELINE_DETAIL = 12
# Share of the endpoint phrase's words a dated event must share to be that endpoint.
ENDPOINT_MATCH = 0.5

AMBIGUITY_HEADER = (
    "تنبيه — لفظ في السؤال يقابله أكثر من شيء موثّق في المصادر.\n"
    "ميّز بينها بتسمية كل واحد كما وردت، ولا ترجّح واحدًا دون الآخر؛ وإن بقي السؤال\n"
    "محتملًا لأكثر من معنى فاذكر الاحتمالين واطلب التوضيح:"
)
MAX_AMBIGUITY_GROUPS = 2
MAX_AMBIGUITY_ENTRIES = 4
CLOCK_TIME = re.compile(r"\b\d{1,2}[:٫.]\d{2}\b|\bالساعه\s*\d{1,2}")
MAX_VALUE_CHARS = 120
GENERIC_LABELS = frozenset(
    {"", "البند", "التفاصيل", "القيمة", "الملاحظات", "النتيجة", "item", "value", "notes"}
)

# Labels that are meaningless without knowing which event they describe.
AMBIGUOUS_LABELS = frozenset(
    normalize(w) for w in (
        "الموعد", "التاريخ", "الوقت", "الساعة", "المكان", "المدة", "الحضور",
        "النتائج", "الحالة", "الوسيلة", "المشاركون", "date", "time", "venue", "status",
    )
)


class FactSheetBuilder:
    def __init__(self, max_facts: int = 45) -> None:
        self.max_facts = max_facts

    @staticmethod
    def section_map(
        sources: list[SourceReference], extra: dict[tuple[str, str], str] | None = None
    ) -> dict[tuple[str, str], SectionSlot]:
        """Which sections each citation stands for.

        A chunk can carry text from a small sibling section that the chunker merged into
        it. That section's own facts are in the context but its id is not the chunk's
        anchor, so it is mapped onto the same citation here — otherwise those facts stay
        invisible to the fact sheet.
        """
        slots: dict[tuple[str, str], SectionSlot] = {}
        for source in sources:
            key = (source.document_id, source.section_id)
            slots[key] = SectionSlot(
                citation=source.citation,
                heading=source.heading or source.section,
                primary=source.tier == EvidenceTier.PRIMARY,
            )
            for (document_id, section_id), heading in (extra or {}).items():
                if document_id != source.document_id:
                    continue
                merged_key = (document_id, section_id)
                if merged_key not in slots:
                    slots[merged_key] = SectionSlot(
                        citation=source.citation, heading=heading, primary=False
                    )
        return slots

    def build(
        self,
        analysis: QueryAnalysis,
        slots: dict[tuple[str, str], SectionSlot],
        entities: list[EntityRecord],
    ) -> tuple[str, list[SourceFact]]:
        if not entities or not slots:
            return "", []

        kinds = self._relevant_kinds(analysis)
        query_terms = content_terms(analysis.question)

        scored: list[tuple[int, SourceFact]] = []
        seen: set[tuple[str, str]] = set()

        for entity in entities:
            if entity.kind not in kinds:
                continue
            label = entity.label.strip()
            value = entity.value.strip()
            if not value or len(value) > MAX_VALUE_CHARS:
                continue
            if normalize(label) in GENERIC_LABELS and entity.kind == "attribute":
                continue
            # A person or company is only a usable fact when the document says what it
            # is. Pattern-matched names carry no label and are often sentence fragments
            # ("الخبير في المحضر"), which crowd the sheet and invite conflation.
            if entity.kind in ("person", "organization") and not label:
                continue

            key = (normalize(label), normalize(value))
            if key in seen:
                continue
            seen.add(key)

            slot = slots.get((entity.document_id, entity.section_id))
            if slot is None:
                continue

            # "الموعد: 20/07/2026 5:30 م" says nothing on its own. Naming its section
            # makes the pairing unambiguous — and lets the question's own words reach it,
            # which is why the meeting's own time never made it into the sheet before.
            display = label
            if normalize(label) in AMBIGUOUS_LABELS and slot.heading:
                display = f"{DECORATION.sub('', slot.heading).strip(' —-*_:')} — {label}"

            priority = self._priority(
                entity, f"{label} {slot.heading}", value, query_terms, slot.primary
            )
            if priority == 0:
                continue

            scored.append((
                priority,
                SourceFact(
                    label=display or entity.kind,
                    value=value,
                    kind=entity.kind,
                    citation=slot.citation,
                    primary=slot.primary,
                    section_heading=slot.heading,
                ),
            ))

        scored.sort(key=lambda item: -item[0])
        facts = [fact for _, fact in scored[: self.max_facts]]

        timeline: list[SourceFact] = []
        ranged = False
        if analysis.intent is Intent.TIMELINE:
            if analysis.is_ranged:
                timeline, ranged = self._ranged_timeline(analysis, slots, entities)
            if not timeline:
                timeline = self.timeline_facts(slots)

        # A timeline question gets the dated skeleton plus only those facts that carry a
        # date or a time of their own. Mixing in dozens of unrelated label/value facts is
        # what made the model pick a few entries and drop the rest, but dropping them all
        # hid the events whose exact time lives in a table row rather than a heading.
        if timeline:
            detail = [] if ranged else [
                f for f in facts
                # A bare date entity adds nothing the skeleton above does not already
                # say, and its label is the literal kind name.
                if f.label != f.kind and self._states_a_time(f)
            ][:MAX_TIMELINE_DETAIL]
            header = RANGE_HEADER if ranged else TIMELINE_HEADER
            lines = "\n".join(
                f"- {f.label}: {f.value}  [{f.citation}]" for f in timeline + detail
            )
            logger.info(
                "Fact sheet: %s dated entries%s + %s timed facts",
                len(timeline), " (ranged, ascending)" if ranged else "", len(detail),
            )
            return f"{header}\n{lines}", timeline + detail

        if not facts:
            return "", []

        lines = "\n".join(f"- {f.label}: {f.value}  [{f.citation}]" for f in facts)
        block = f"{HEADER}\n{lines}"

        ambiguity = self._ambiguity_note(query_terms, facts)
        if ambiguity:
            block = f"{ambiguity}\n\n{block}"

        logger.info("Fact sheet: %s facts from %s entities", len(facts), len(entities))
        return block, facts

    @staticmethod
    def _ambiguity_note(query_terms: set[str], facts: list[SourceFact]) -> str:
        """Warn when a word in the question names more than one documented thing.

        One word may name two documented things — "رخصة <جهة>" can match both a trade licence
        and a building permit. Left alone the
        model picks one and states it as the answer; naming both keeps the distinction
        the document draws instead of guessing which was meant.
        """
        groups: list[tuple[str, list[SourceFact]]] = []
        for term in sorted(t for t in query_terms if len(t) > 3):
            matched, seen = [], set()
            for fact in facts:
                if term not in content_terms(fact.label):
                    continue
                key = (normalize(fact.label), normalize(fact.value))
                if key in seen:
                    continue
                seen.add(key)
                matched.append(fact)
            if len({normalize(f.label) for f in matched}) >= 2:
                groups.append((term, matched[:MAX_AMBIGUITY_ENTRIES]))

        if not groups:
            return ""

        lines = [AMBIGUITY_HEADER]
        for term, matched in groups[:MAX_AMBIGUITY_GROUPS]:
            lines.append(f"«{term}» يقابلها في المصادر:")
            lines.extend(f"  - {f.label}: {f.value}  [{f.citation}]" for f in matched)
        return "\n".join(lines)

    def _ranged_timeline(
        self,
        analysis: QueryAnalysis,
        slots: dict[tuple[str, str], SectionSlot],
        entities: list[EntityRecord],
    ) -> tuple[list[SourceFact], bool]:
        """Events between the two ends the question names, in the order they happened.

        "من بدء الحفر حتى الانهيار" asks for a window. Handing the model the whole
        history newest-first led it out of that window and past the end date, so the
        span is resolved against the evidence, clipped to it, and ordered forwards.
        """
        entries = self._dated_entries(slots, entities)
        if not entries:
            return [], False

        start = self._resolve_endpoint(analysis.range_from, entries)
        end = self._resolve_endpoint(analysis.range_to, entries)
        if start is None or end is None:
            return [], False
        if start > end:
            start, end = end, start

        inside = [item for item in entries if start <= item[0] <= end]
        if len(inside) < 2:
            return [], False

        inside.sort(key=lambda item: item[0])
        return [fact for _, fact in inside[:MAX_TIMELINE_ENTRIES]], True

    @staticmethod
    def _resolve_endpoint(
        phrase: str, entries: list[tuple[tuple[int, int, int], SourceFact]]
    ) -> tuple[int, int, int] | None:
        """Which dated event the phrase refers to, by shared wording."""
        wanted = {t for t in content_terms(phrase) if len(t) > 2}
        if not wanted:
            return None
        best, best_score = None, 0.0
        for when, fact in entries:
            terms = content_terms(f"{fact.label} {fact.value}")
            score = len(wanted & terms) / len(wanted)
            if score > best_score:
                best, best_score = when, score
        return best if best_score >= ENDPOINT_MATCH else None

    def _dated_entries(
        self,
        slots: dict[tuple[str, str], SectionSlot],
        entities: list[EntityRecord],
    ) -> list[tuple[tuple[int, int, int], SourceFact]]:
        """Every dated event in the evidence: section headings and dated table rows."""
        entries: list[tuple[tuple[int, int, int], SourceFact]] = []
        seen: set[str] = set()

        for when, fact in self._heading_entries(slots):
            key = f"{fact.label}|{normalize(fact.value)}"
            if key not in seen:
                seen.add(key)
                entries.append((when, fact))

        for entity in entities:
            when = parse_date(entity.label)
            if when is None:
                continue
            slot = slots.get((entity.document_id, entity.section_id))
            value = entity.value.strip()
            if slot is None or not value or len(value) > MAX_VALUE_CHARS:
                continue
            label = " ".join(extract_dates(entity.label)[:1])
            key = f"{label}|{normalize(value)}"
            if key in seen:
                continue
            seen.add(key)
            entries.append((
                when,
                SourceFact(label=label, value=value, kind="date", citation=slot.citation,
                           primary=slot.primary, section_heading=slot.heading),
            ))
        return entries

    @staticmethod
    def _states_a_time(fact: SourceFact) -> bool:
        """Whether the fact pins an event to a date or a clock time."""
        text = f"{fact.label} {fact.value}"
        return bool(parse_date(text) or CLOCK_TIME.search(normalize(text)))

    @classmethod
    def timeline_facts(cls, slots: dict[tuple[str, str], SectionSlot]) -> list[SourceFact]:
        """Dated section headings as a newest-first skeleton, for "آخر التحديثات"."""
        entries = cls._heading_entries(slots)
        entries.sort(key=lambda item: item[0], reverse=True)
        return [fact for _, fact in entries[:MAX_TIMELINE_ENTRIES]]

    @staticmethod
    def _heading_entries(
        slots: dict[tuple[str, str], SectionSlot],
    ) -> list[tuple[tuple[int, int, int], SourceFact]]:
        """Update sections state their date in the heading and nowhere else, so the
        heading itself is the fact; without it the model sees a section's body with no
        idea when it happened."""
        entries: list[tuple[tuple[int, int, int], SourceFact]] = []
        seen: set[str] = set()

        for slot in slots.values():
            when = parse_date(slot.heading)
            if when is None:
                continue
            label = " ".join(extract_dates(slot.heading)[:1]) or "بتاريخ"
            # Keep the heading intact apart from decoration — cutting the date out of it
            # leaves fragments like "(أودع— 24 صفحة)" that the model then repeats.
            title = DECORATION.sub("", slot.heading).strip(" —-*_:")
            key = f"{label}|{normalize(title)}"
            if not title or key in seen:
                continue
            seen.add(key)
            entries.append((
                when,
                SourceFact(
                    label=label, value=title[:MAX_VALUE_CHARS], kind="date",
                    citation=slot.citation, primary=slot.primary,
                    section_heading=slot.heading,
                ),
            ))
        return entries

    @staticmethod
    def _relevant_kinds(analysis: QueryAnalysis) -> tuple[str, ...]:
        if analysis.exhaustive or analysis.role_specific:
            return ROLE_KINDS + VALUE_KINDS
        preferred = tuple(analysis.preferred_entity_kinds)
        return preferred + ("attribute",) if "attribute" not in preferred else preferred

    @staticmethod
    def _priority(
        entity: EntityRecord,
        label: str,
        value: str,
        query_terms: set[str],
        in_primary: bool,
    ) -> int:
        """Higher wins. A labelled fact from a primary section is the strongest signal."""
        matches_query = bool(
            query_terms & (content_terms(label) | content_terms(value))
        )
        if in_primary and label:
            return 5 if matches_query else 4
        if in_primary:
            return 3 if matches_query else 2
        if matches_query and label:
            return 2
        return 1 if matches_query else 0
