"""Which version of a fact is the one still in force.

A working file does not replace what it corrects — it appends. The original claim stays
on the page, the correction is written underneath it, and both remain retrievable
forever. Measured on this corpus, one question had three answers living side by side:

    "مأمورية الخبير (5 بنود)"                        — an early statement
    the seven items requested in the pleadings        — the original request
    "المأمورية النافذة = 3 بنود فقط"                  — the one actually in force

Retrieval found all three and the model answered with whichever came first. Asking the
same question three ways produced 7, then 5, then 3 — and only the phrasing that
happened to contain the word "النافذة" got it right.

Dates do not settle this. The three passages sit in sections with no dates of their own,
and the correcting passage is not always the newest thing written about a subject. What
*does* settle it is that documents say so in words: a passage that carries the
correction announces itself — "النافذة", "المصحح", "تصحيحاً لخطأ سابق" — and a passage
that has been retired is usually marked as retired.

So this reads those words. Two small, high-precision lists and nothing else:

* a passage asserting that *it* is the version in force earns a boost;
* a passage marked as retired loses the same amount;
* everything else — the overwhelming majority — is untouched.

The weight is set below lexical relevance on purpose. This decides between passages that
are already competing to answer one question; it must never drag an unrelated passage
forward because somebody wrote "النسخة النهائية" in its heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import normalize

#: Wordings by which a passage asserts that it states the position now in force.
#:
#: Deliberately narrow. "جديد" and "محدَّث" are not here: every working file is full of
#: them, and they describe when something was written rather than whether it still
#: stands. What earns a place is a word that answers "is this the one that counts?".
CURRENT_CUES: tuple[str, ...] = (
    # Arabic — explicitly in force
    "النافذه",
    "النافذ",
    "الناقذه",          # a common typo for النافذة, seen in real files
    "الساريه",
    "الساري",
    "المعمول به",
    "المعمول بها",
    # Arabic — this passage carries a correction
    "المصحح",
    "المصححه",
    "بعد التصحيح",
    "تصحيحا لخطا سابق",
    "تصحيح خطا سابق",
    "قراءه نهائيه",
    "تحقق حاسم",
    "النسخه النهائيه",
    "الصيغه النهائيه",
    "المعتمده نهائيا",
    "المعتمد نهائيا",
    "يعتمد عليه",
    "يعتد به",
    "التذكيرات الدائمه",
    # English
    "in force",
    "currently in force",
    "effective version",
    "corrected version",
    "final version",
    "as corrected",
    "supersedes",
    "authoritative",
)

#: Wordings by which a passage marks *itself* as no longer the position in force.
#:
#: "محذوف" is deliberately absent. It appears inside the correcting passage — "البند 4
#: محذوف" — which is the authority, not the casualty. Treating it as a retirement marker
#: would push down the very passage that settles the question.
SUPERSEDED_CUES: tuple[str, ...] = (
    "ملغى",
    "ملغاه",
    "ملغي",
    "لم يعد ساريا",
    "لم تعد ساريه",
    "لم يعد مطلوبا",
    "نسخه قديمه",
    "الاصدار السابق",
    "النسخه السابقه",
    "الصيغه السابقه",
    "مستبدل",
    "مستبدله",
    "استبدل بـ",
    "قبل التصحيح",
    "الصيغه المرفوضه",
    "superseded",
    "superseded by",
    "obsolete",
    "deprecated",
    "no longer in force",
    "no longer applies",
    "replaced by",
    "previous version",
    "earlier version",
)

#: Arabic needs the boundaries stated rather than left to `\b`, for the same reason the
#: temporal cues do: short particles sit inside longer words constantly.
_LETTER = r"[\w؀-ۿ]"


def _pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(normalize(c)) for c in sorted(cues, key=len, reverse=True)
    )
    return re.compile(f"(?<!{_LETTER})(?:{alternatives})(?!{_LETTER})")


CURRENT_PATTERN = _pattern(CURRENT_CUES)
SUPERSEDED_PATTERN = _pattern(SUPERSEDED_CUES)

#: How far into a passage to read. A statement about which version is in force is made
#: where the passage introduces itself, not buried in its ninth table row; reading the
#: whole body would let a passing mention of "الإصدار السابق" halfway down retire a
#: passage that was never retired.
LEAD_CHARS = 700


@dataclass(frozen=True, slots=True)
class Supersession:
    """Whether a passage claims to be current, claims to be retired, or says neither."""

    #: "current" | "superseded" | ""
    state: str = ""
    cue: str = ""

    @property
    def is_current(self) -> bool:
        return self.state == "current"

    @property
    def is_superseded(self) -> bool:
        return self.state == "superseded"

    def describe(self) -> str:
        if self.is_current:
            return f"نسخة سارية («{self.cue}»)"
        if self.is_superseded:
            return f"نسخة ملغاة («{self.cue}»)"
        return ""


def read_status(section: str = "", heading: str = "", content: str = "") -> Supersession:
    """What a passage says about its own standing.

    The heading is read in full and the body only as far as its opening, because that is
    where a document states which version it is. Retirement is checked first: a passage
    that says it has been replaced is replaced, whatever else it also claims.
    """
    head = normalize(f"{section} {heading}")
    lead = normalize(content[:LEAD_CHARS])
    text = f"{head} {lead}"

    match = SUPERSEDED_PATTERN.search(text)
    if match:
        return Supersession(state="superseded", cue=match.group(0))

    match = CURRENT_PATTERN.search(text)
    if match:
        return Supersession(state="current", cue=match.group(0))

    return Supersession()
