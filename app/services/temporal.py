"""Reading time out of a question, and out of the evidence it will be answered from.

A retrieval system with no sense of time answers "what is the latest position?" from
whatever happens to match the words, and sounds exactly as certain as it does when
quoting a clause. That failure is worse than a refusal, because the reader has no way to
see that the answer stopped a week early.

The rule this module exists to enforce is narrow on purpose:

    prefer newer evidence *only* when the question asked for the newest.

Not otherwise. A question about what was agreed originally must still reach the original
agreement, and a question naming a date must reach that date rather than the most recent
one — an explicit date is a better statement of intent than any cue this file can read,
so it switches the generic preference off entirely.

Everything here is deterministic: a fixed cue list matched at word boundaries, and dates
parsed by the same code the rest of the system uses. No model, no learned threshold, and
the cue that fired is carried through to the trace so a boost can always be explained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.core.text import all_dates, normalize
from app.services.relative_dates import DateWindow, read_relative

#: Wordings that ask for the most recent state of something. Singular *and* plural:
#: the first version of this only had the plural "آخر تحديثات", so "أحدث تحديث" — the
#: same question asked once instead of repeatedly — reached none of the dated sections.
LATEST_CUES: tuple[str, ...] = (
    # Arabic
    "اخر",            # آخر مرحلة / آخر تحديث / آخر قرار
    "احدث",           # أحدث تحديث / أحدث قرار
    "الاحدث",
    "الاخيره",
    "الاخير",
    "المستجدات",
    "مستجدات",
    "تطورات",
    "الوضع الحالي",
    "الوضع الراهن",
    "الحالي",
    "الحاليه",
    "الراهن",
    "الراهنه",
    "حاليا",
    "الان",
    "حتى الان",
    "لغايه الان",
    "ما وصلنا اليه",
    "اين وصلنا",
    "ما استجد",
    "ماذا استجد",
    "النهائيه المعتمده",
    # English
    "latest",
    "most recent",
    "newest",
    "current",
    "currently",
    "current status",
    "current state",
    "right now",
    "up to date",
    "so far",
    "as things stand",
    "where do we stand",
    "last update",
    "recent",
)

#: Wordings that ask about the beginning of something. They do not merely cancel the
#: preference — they reverse it, because "what was originally agreed" is answered by the
#: oldest evidence and the newest evidence is the wrong answer to it.
EARLIEST_CUES: tuple[str, ...] = (
    "اول",
    "الاولى",
    "الاول",
    "البدايه",
    "في البدايه",
    "بدايه",
    "الاصلي",
    "الاصليه",
    "اصلا",
    "سابقا",
    "في السابق",
    "الاولي",
    "المبدئي",
    "المبدئيه",
    "original",
    "originally",
    "initial",
    "initially",
    "at the start",
    "first",
    "earliest",
    "previously",
    "originally agreed",
)

#: Matched at word boundaries. Arabic needs this stated rather than left to `\b`:
#: "الآن" normalises to "الان", which is a substring of "الانصراف", and "آخر" to "اخر",
#: which sits inside "الاخر" — the *other* party, not the last one. Both would fire a
#: temporal boost on a question that has nothing to do with time.
_LETTER = r"[\w؀-ۿ]"


def _cue_pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(normalize(c)) for c in sorted(cues, key=len, reverse=True)
    )
    return re.compile(f"(?<!{_LETTER})(?:{alternatives})(?!{_LETTER})")


LATEST_PATTERN = _cue_pattern(LATEST_CUES)
EARLIEST_PATTERN = _cue_pattern(EARLIEST_CUES)


@dataclass(frozen=True, slots=True)
class TemporalIntent:
    """What, if anything, the question said about time.

    `cue` carries the exact wording that fired, so a ranking boost can be justified in
    the trace by quoting the question rather than by asserting that a rule applied.
    """

    wants_latest: bool = False
    wants_earliest: bool = False
    #: Dates the question named itself. Their presence turns the generic preference off.
    explicit_dates: tuple[tuple[int, int, int], ...] = ()
    cue: str = ""
    #: A period the question named relatively — "الأسبوع الماضي" — resolved to dates.
    window: DateWindow | None = None

    @property
    def is_temporal(self) -> bool:
        return self.wants_latest or self.wants_earliest or self.window is not None

    def describe(self) -> str:
        if self.explicit_dates:
            spelled = ", ".join(f"{d:02d}/{m:02d}/{y}" for y, m, d in self.explicit_dates)
            return f"تاريخ صريح في السؤال ({spelled}) — لا تفضيل زمني عام"
        if self.window is not None:
            return f"فترة نسبية في السؤال: {self.window.describe()}"
        if self.wants_latest:
            return f"السؤال يطلب الأحدث («{self.cue}»)"
        if self.wants_earliest:
            return f"السؤال يطلب الأقدم («{self.cue}»)"
        return ""


def read_intent(question: str) -> TemporalIntent:
    """What the question asks for in time. Deterministic, and cheap enough to always run.

    Precedence, in order:

    1. An explicit date beats everything. Someone who names a date has stated their
       intent more precisely than any cue list can infer it, and quietly preferring the
       newest evidence would answer a different question from the one asked.
    2. Otherwise a relative period — "الأسبوع الماضي", "آخر ثلاثين يومًا" — resolved
       against today. It is more precise than "latest": it says which stretch of time,
       not merely that the newest end is wanted.
    3. Otherwise a "latest" cue wins over an "earliest" one. A question carrying both —
       "من أول جلسة حتى آخر حكم" — is a span, and the dated sweep covers a span from its
       newest end; starting at the oldest would truncate exactly the part that is usually
       missing.
    4. Otherwise, no temporal preference at all.
    """
    normalised = normalize(question)

    named = tuple(sorted(all_dates(question)))
    if named:
        return TemporalIntent(explicit_dates=named)

    window = read_relative(question)
    if window is not None:
        return TemporalIntent(window=window, cue=window.phrase)

    latest = LATEST_PATTERN.search(normalised)
    earliest = EARLIEST_PATTERN.search(normalised)

    if latest:
        return TemporalIntent(wants_latest=True, cue=latest.group(0))
    if earliest:
        return TemporalIntent(wants_earliest=True, cue=earliest.group(0))
    return TemporalIntent()


def evidence_date(*parts: str) -> tuple[int, int, int] | None:
    """The date a passage is about, or None when it states none.

    Read from the parts in the order given — heading first in practice, because a dated
    section says its date in its title and the body then talks about what happened. The
    newest date within a part wins: a section describing an event often also cites the
    older documents leading up to it, and the section is about the event.

    Future dates are discarded. An expiry, a licence valid until 2029 or a scheduled
    hearing is not evidence of what has happened, and left in it would sort above every
    real event and take every slot reserved for the newest.
    """
    today = date.today().timetuple()[:3]
    for part in parts:
        if not part:
            continue
        candidates = [d for d in all_dates(part) if d <= today]
        if candidates:
            return max(candidates)
    return None


#: Days after which a piece of evidence has lost half of its recency advantage. Chosen
#: so that within one working month everything stays competitive and a year-old passage
#: keeps only a token share — a question about the current position is usually answered
#: by something from the last few weeks, not by a decay curve tuned to the day.
HALF_LIFE_DAYS = 45.0


def recency_weight(
    when: tuple[int, int, int] | None, newest: tuple[int, int, int] | None
) -> float:
    """How much newer-is-better credit a passage earns, from 0.0 to 1.0.

    Measured against the newest dated evidence in the same candidate pool rather than
    against today, so a corpus whose latest entry is six months old still ranks its own
    newest material first instead of flattening everything to zero.

    An undated passage scores zero. It is not penalised — it simply earns nothing here,
    which leaves it ranked on its own merits by every other feature.
    """
    if when is None or newest is None:
        return 0.0
    try:
        gap = (date(*newest) - date(*when)).days
    except ValueError:
        return 0.0
    if gap <= 0:
        return 1.0
    return 0.5 ** (gap / HALF_LIFE_DAYS)


def age_weight(
    when: tuple[int, int, int] | None, oldest: tuple[int, int, int] | None
) -> float:
    """The mirror of `recency_weight`, for questions asking what came first."""
    if when is None or oldest is None:
        return 0.0
    try:
        gap = (date(*when) - date(*oldest)).days
    except ValueError:
        return 0.0
    if gap <= 0:
        return 1.0
    return 0.5 ** (gap / HALF_LIFE_DAYS)


def matches_explicit_date(
    when: tuple[int, int, int] | None, wanted: tuple[tuple[int, int, int], ...]
) -> bool:
    """Whether a passage carries one of the dates the question named."""
    return bool(when and wanted and when in wanted)
