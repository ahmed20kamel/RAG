"""Statements a document makes about which of its own versions is the one in force.

A working file corrects itself by appending. The original claim stays on the page, the
correction is written underneath it, and both remain retrievable for ever. Measured on
this corpus, one question had three live answers — seven items, five items, three items
— and which one came back depended on how the question was phrased.

Ranking was the wrong place to fix it, and the measurement said so. Demoting the
superseded versions worked for the question it was built for and, at the threshold loose
enough to catch it, marked half a document as superseded: a page of standing reminders
shares six content words with almost everything else in the same file, and a question
about excavation inspection forms came back with no evidence at all. Tightened until it
was safe, it no longer caught the case it existed for.

So this does not touch ranking. When a passage states plainly that a particular value is
the one in force — "<البند> النافذ = <القيمة>" — that sentence is lifted out and put
in front of the model as its own line, above the sources. It arrives whether or not the
passage that carried it ranked first, which is the whole point: the answer stops
depending on the ordering of evidence that all of it can see anyway.

Two properties this is built to hold.

**It quotes, never paraphrases.** Every line is a span copied from the retrieved text
with its citation attached. Nothing is summarised, inferred or recomposed, so the block
cannot introduce a claim the documents do not make.

**It stays silent unless a document is explicit.** No cue, no line. For the overwhelming
majority of questions this produces nothing at all and the prompt is byte-identical to
what it was — which is what the golden set checks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import or_, select

from app.core.text import normalize
from app.models.database import session_scope
from app.models.knowledge import ChunkRecord

logger = logging.getLogger(__name__)

#: Wordings by which a sentence declares a value to be the one currently in force.
#:
#: Narrow on purpose. "جديد" and "محدَّث" are absent: they say when something was
#: written, not whether it still stands, and every working file is full of them.
IN_FORCE_CUES: tuple[str, ...] = (
    "النافذه",
    "النافذ",
    "الساريه",
    "الساري",
    "المعمول به",
    "المعمول بها",
    "المعتمده نهائيا",
    "المعتمد نهائيا",
    "بعد التصحيح",
    "المصحح",
    "المصححه",
    "يعتد به",
    "يعتمد عليه",
    "in force",
    "currently in force",
    "as corrected",
    "effective version",
)

#: Wordings by which a sentence retires part of a document.
DELETION_CUES: tuple[str, ...] = (
    "محذوف",
    "محذوفه",
    "محذوفان",
    "ملغى",
    "ملغاه",
    "لم يعد ساريا",
    "لم تعد ساريه",
    "شطب",
    "deleted",
    "removed",
    "struck out",
    "no longer in force",
)

_LETTER = r"[\w؀-ۿ]"


def _pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(normalize(c)) for c in sorted(cues, key=len, reverse=True)
    )
    return re.compile(f"(?<!{_LETTER})(?:{alternatives})(?!{_LETTER})")


IN_FORCE_PATTERN = _pattern(IN_FORCE_CUES)
DELETION_PATTERN = _pattern(DELETION_CUES)

#: A sentence, a table row or a bullet. Split on the boundaries a document actually
#: uses rather than on full stops alone, because the statements this looks for are
#: written as table rows at least as often as prose.
SEGMENT = re.compile(r"[\n\r]+|(?<=[.؟!])\s+")

#: Too short to carry a claim; too long to be one sentence, and quoting a paragraph
#: would put the surrounding argument in the block along with the statement.
MIN_LINE = 12
MAX_LINE = 320

#: A statement without a value is an assertion about nothing. "النسخة النافذة معتمدة"
#: tells a reader nothing they can act on; "النطاق النافذ = 3 بنود" does.
HAS_VALUE = re.compile(r"\d")

#: How many lines may reach the prompt. Enough for a correction and the deletions that
#: come with it; past that the block would start competing with the evidence it exists
#: to qualify.
MAX_LINES = 8
#: Markdown table plumbing and strike markers, removed so a quoted row reads as a
#: sentence. The words are untouched.
NOISE = re.compile(r"[|~*`>]+")


@dataclass
class InForceStatement:
    """One sentence a document wrote about which of its versions counts."""

    text: str
    citation: int
    cue: str
    kind: str = "in_force"  # in_force | deletion

    #: Where it came from, when it was not one of the numbered sources.
    document: str = ""

    def describe(self) -> str:
        where = f"[{self.citation}]" if self.citation else f"[{self.document}]"
        return f"{self.text} {where}"


@dataclass
class InForceSet:
    """Everything the retrieved evidence declared about its own currency."""

    statements: list[InForceStatement] = field(default_factory=list)

    def render(self) -> str:
        """The block that goes into the prompt, or nothing at all.

        Kept apart from the facts block, and above it, for the same reason the
        calculated values are kept apart: these are not more facts, they are the
        document saying which of its facts to believe. A reader — and a model — has to
        be told which kind of statement it is looking at.
        """
        if not self.statements:
            return ""
        # The wording is directive because a description is not enough. Told merely
        # that these lines "determine which version applies", a model tends to lead
        # with the superseded value and add the correction as a footnote — complete,
        # and misread by anyone skimming. Saying plainly what to put first prevents it.
        lines = [
            "=== النسخة النافذة — ما يقوله المستند عن نفسه (منقول حرفيًا) ===",
            "(إلزامي: هذه العبارات تُبيّن أي نسخة من المعلومة سارية الآن. إن تعارضت مع "
            "أي شيء في المصادر أدناه، فهذه هي الصحيحة والمصادر أقدم منها.",
            "ابدأ إجابتك بما تقوله هذه العبارات. إن ذكرت النسخة الأقدم فاذكرها بعدها "
            "صراحةً بوصفها «نسخة سابقة تجاوزها التصحيح»، ولا تقدّمها على أنها الجواب.)",
        ]
        lines.extend(f"- {s.describe()}" for s in self.statements)
        return "\n".join(lines) + "\n\n"


def _clean(line: str) -> str:
    return NOISE.sub(" ", line).strip(" -—:\t")


def extract(sources: list) -> InForceSet:
    """Statements about currency, quoted from the retrieved passages.

    Reads the same excerpts the model is about to read, so nothing enters the block that
    is not already in the prompt. A line qualifies only when it carries a currency or
    deletion cue *and* a number — a declaration with no value in it is a claim about
    nothing, and quoting it would add words without adding information.
    """
    found: list[InForceStatement] = []
    seen: set[str] = set()

    for source in sources:
        excerpt = getattr(source, "excerpt", "") or ""
        citation = int(getattr(source, "citation", 0) or 0)
        for raw in SEGMENT.split(excerpt):
            line = _clean(raw)
            if not (MIN_LINE <= len(line) <= MAX_LINE) or not HAS_VALUE.search(line):
                continue

            normalised = normalize(line)
            match = IN_FORCE_PATTERN.search(normalised)
            kind = "in_force"
            if not match:
                match = DELETION_PATTERN.search(normalised)
                kind = "deletion"
            if not match:
                continue

            key = normalised[:120]
            if key in seen:
                continue
            seen.add(key)
            found.append(
                InForceStatement(text=line, citation=citation, cue=match.group(0), kind=kind)
            )

    # Currency statements first: they say what *is*, and the deletions that follow say
    # what is not, which only means something once the reader knows the former. The list
    # is then truncated rather than discarded: dropping the whole block once it grows
    # past the cap would throw away the lines that settle the question, just because
    # passages elsewhere use the word "محذوف" in passing.
    found.sort(key=lambda s: (s.kind != "in_force", s.citation))
    return InForceSet(statements=found[:MAX_LINES])


#: Cue words as they appear in the stored text, for the index lookup. Matching is done
#: properly afterwards; this only narrows 300 rows to a handful before the real work.
_SQL_CUES = ("النافذة", "النافذ", "السارية", "المصحح", "محذوف", "ملغى", "بعد التصحيح")


def _lines_from(text: str, citation: int, document: str, seen: set[str]) -> list[InForceStatement]:
    """Qualifying lines in one passage. Shared by both readers below."""
    out: list[InForceStatement] = []
    for raw in SEGMENT.split(text or ""):
        line = _clean(raw)
        if not (MIN_LINE <= len(line) <= MAX_LINE) or not HAS_VALUE.search(line):
            continue
        normalised = normalize(line)
        match = IN_FORCE_PATTERN.search(normalised)
        kind = "in_force"
        if not match:
            match = DELETION_PATTERN.search(normalised)
            kind = "deletion"
        if not match:
            continue
        key = normalised[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            InForceStatement(
                text=line, citation=citation, cue=match.group(0), kind=kind, document=document
            )
        )
    return out


def extract_for_documents(sources: list, document_ids: list[str]) -> InForceSet:
    """Currency statements from the whole document, not only the passages that ranked.

    This is the correction that made the mechanism work. Reading the retrieved excerpts
    alone inherited the very problem it was built to solve: when the passage carrying
    the passage carrying "النافذة = <القيمة>" did not rank, there was nothing to lift, and the
    block was empty on exactly the questions that needed it.

    So the documents already in play are searched directly for sentences that declare
    which version is in force. The search is narrow — a handful of cue words over the
    chunks of a few documents — and the result is quoted, never summarised. Statements
    found in a numbered source keep its citation; the rest are attributed to their file,
    because presenting an uncited line as though it carried a citation would be the one
    thing this block must never do.
    """
    seen: set[str] = set()
    found: list[InForceStatement] = []

    for source in sources:
        found += _lines_from(
            getattr(source, "excerpt", "") or "",
            int(getattr(source, "citation", 0) or 0),
            getattr(source, "filename", "") or "",
            seen,
        )

    if document_ids:
        try:
            with session_scope() as db:
                rows = db.scalars(
                    select(ChunkRecord)
                    .where(
                        ChunkRecord.document_id.in_(document_ids),
                        or_(*(ChunkRecord.content.contains(c) for c in _SQL_CUES)),
                    )
                    .limit(60)
                ).all()
            for row in rows:
                found += _lines_from(row.content, 0, row.filename, seen)
        except Exception:  # noqa: BLE001
            # A block that could not be built is a block that is absent, never an
            # answer that fails: the evidence is in the prompt either way.
            logger.exception("Could not read currency statements from the index")

    found.sort(key=lambda s: (s.kind != "in_force", s.citation == 0, s.citation))
    return InForceSet(statements=found[:MAX_LINES])
