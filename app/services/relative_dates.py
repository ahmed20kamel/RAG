"""Relative time in a question — "الأسبوع الماضي", "قبل ثلاثة أيام" — as real dates.

Two things are needed, and without either these questions cannot be answered:

* **The model does not know today's date** unless the prompt states it. Without it,
  "what happened last week" has no referent: the model either guesses a week or
  describes the newest evidence and implies it was last week's.
* **Retrieval cannot use the phrase directly.** "الأسبوع الماضي" shares no word with a
  section titled "جلسة 24/09/2026", so the dated section that answers the question
  would be reachable only by luck.

This module turns the phrase into a closed window of dates against a stated "today".
Ranking then rewards passages dated inside the window exactly as it rewards a passage
stating a date the question named, and the prompt states both the window and today's
date, so the model reads "الأسبوع الماضي = 21/09/2026 – 27/09/2026" instead of inferring it.

Weeks run Monday to Sunday, the UAE working week since 2022.

What is deliberately not read as a window:

* "اليوم", "الآن" — in "ما الوضع اليوم" they mean *currently*, which is the latest-intent
  reader's job. A one-day window would tell the model to report only events dated today.
* "قبل ثلاثين يومًا من انتهاء العقد", "أكثر من ثلاثين يومًا" — a deadline measured from
  an event, not a distance back from today. Contracts are full of them.
* "آخر مرحلة" — "latest". "آخر" becomes a window only with a count and a unit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from app.core.number_words import spelled_numbers
from app.core.text import normalize


@dataclass(frozen=True, slots=True)
class DateWindow:
    """A closed range of days a question referred to."""

    phrase: str
    start: date
    end: date
    today: date

    def contains(self, day: tuple[int, int, int] | date | None) -> bool:
        if day is None:
            return False
        if isinstance(day, tuple):
            try:
                day = date(*day)
            except ValueError:
                return False
        return self.start <= day <= self.end

    @property
    def single_day(self) -> bool:
        return self.start == self.end

    def label(self) -> str:
        if self.single_day:
            return _fmt(self.start)
        return f"من {_fmt(self.start)} إلى {_fmt(self.end)}"

    def describe(self) -> str:
        return f"«{self.phrase}» = {self.label()}"

    def prompt_block(self) -> str:
        """The lines that tell the model what the phrase meant, stated not inferred."""
        return (
            "=== مرجع زمني (محسوب من تاريخ اليوم، لا من المستندات) ===\n"
            f"تاريخ اليوم: {_fmt(self.today)}.\n"
            f"{self.describe()}.\n"
            "(اعتمد هذه الفترة لتحديد الوقائع المقصودة بالسؤال. إن لم تذكر المصادر "
            "أي واقعة داخلها فقل ذلك صراحةً، ولا تقدّم واقعة خارجها على أنها ضمنها.)\n\n"
        )


def _fmt(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year}"


def _week_of(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _month_of(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    following = (start + timedelta(days=32)).replace(day=1)
    return start, following - timedelta(days=1)


def _year_of(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    last = _month_of(date(year, month + 1, 1))[1].day
    return date(year, month + 1, min(day.day, last))


def _shift_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


# -- vocabulary, normalised ----------------------------------------------------------

_L = r"(?<![\w؀-ۿ])"
_R = r"(?![\w؀-ۿ])"

_PAST = (r"(?:الماضي|الماضيه|الفايت|الفايته|المنصرم|المنصرمه|السابق|السابقه"
         r"|اللي فات|اللي فاتت|الي فات|الي فاتت)")
_NEXT = r"(?:القادم|القادمه|المقبل|المقبله|الجاي|الجايه|التالي|التاليه)"
_THIS = r"(?:الحالي|الحاليه|الجاري|الجاريه|ده|دا|دي)"

#: How many days one of each unit spans, and which calendar unit it is.
_UNITS: dict[str, str] = {
    "يوم": "day", "ايام": "day", "يوما": "day",
    "اسبوع": "week", "اسابيع": "week", "اسبوعا": "week",
    "شهر": "month", "اشهر": "month", "شهور": "month", "شهرا": "month",
    "سنه": "year", "سنوات": "year", "سنين": "year", "عام": "year", "اعوام": "year", "عاما": "year",
    "day": "day", "days": "day", "week": "week", "weeks": "week",
    "month": "month", "months": "month", "year": "year", "years": "year",
}
_DUALS: dict[str, str] = {
    "يومين": "day", "يومان": "day", "اسبوعين": "week", "اسبوعان": "week",
    "شهرين": "month", "شهران": "month", "سنتين": "year", "سنتان": "year",
    "عامين": "year", "عامان": "year",
}
_UNIT_WORD = "|".join(sorted(_UNITS, key=len, reverse=True))
_DUAL_WORD = "|".join(sorted(_DUALS, key=len, reverse=True))

#: A count followed by one of these is a deadline measured from an event, not a
#: distance back from today: "قبل ثلاثين يومًا من انتهاء العقد".
_FROM_EVENT = r"(?!\s+(?:من|علي|قبل|بعد|of|before|after|from|prior))"
_COUNT = r"(\d+|[\w؀-ۿ]+(?:\s+و?[\w؀-ۿ]+){0,3}?)"
_EN_COUNT = r"(\d+|[a-z]+(?:[- ][a-z]+)?)"


def _fixed_rules(today: date) -> list[tuple[str, Callable[[], tuple[date, date]]]]:
    yesterday = today - timedelta(days=1)
    return [
        (rf"{_L}(?:اول امس|اول من امس|قبل امس){_R}",
         lambda: (today - timedelta(days=2), today - timedelta(days=2))),
        (rf"{_L}(?:امس|البارحه|امبارح){_R}", lambda: (yesterday, yesterday)),
        (rf"{_L}(?:غدا|الغد|بكره|بكرا){_R}",
         lambda: (today + timedelta(days=1), today + timedelta(days=1))),
        (rf"{_L}الاسبوع\s+{_PAST}{_R}", lambda: _week_of(today - timedelta(days=7))),
        (rf"{_L}الاسبوع\s+{_NEXT}{_R}", lambda: _week_of(today + timedelta(days=7))),
        (rf"{_L}(?:هذا الاسبوع|الاسبوع\s+{_THIS}){_R}", lambda: _week_of(today)),
        (rf"{_L}الشهر\s+{_PAST}{_R}", lambda: _month_of(_shift_months(today, -1))),
        (rf"{_L}الشهر\s+{_NEXT}{_R}", lambda: _month_of(_shift_months(today, 1))),
        (rf"{_L}(?:هذا الشهر|الشهر\s+{_THIS}){_R}", lambda: _month_of(today)),
        (rf"{_L}(?:السنه|العام)\s+{_PAST}{_R}", lambda: _year_of(today.year - 1)),
        (rf"{_L}(?:السنه|العام)\s+{_NEXT}{_R}", lambda: _year_of(today.year + 1)),
        (rf"{_L}(?:هذه السنه|هذا العام|السنه\s+{_THIS}|العام\s+{_THIS}){_R}",
         lambda: _year_of(today.year)),
        (r"\bday before yesterday\b",
         lambda: (today - timedelta(days=2), today - timedelta(days=2))),
        (r"\byesterday\b", lambda: (yesterday, yesterday)),
        (r"\btomorrow\b", lambda: (today + timedelta(days=1), today + timedelta(days=1))),
        (r"\blast week\b", lambda: _week_of(today - timedelta(days=7))),
        (r"\bnext week\b", lambda: _week_of(today + timedelta(days=7))),
        (r"\bthis week\b", lambda: _week_of(today)),
        (r"\blast month\b", lambda: _month_of(_shift_months(today, -1))),
        (r"\bnext month\b", lambda: _month_of(_shift_months(today, 1))),
        (r"\bthis month\b", lambda: _month_of(today)),
        (r"\blast year\b", lambda: _year_of(today.year - 1)),
        (r"\bthis year\b", lambda: _year_of(today.year)),
    ]


def _fixed(normalised: str, today: date) -> tuple[str, date, date] | None:
    """Phrases that name a fixed period relative to today."""
    for pattern, window in _fixed_rules(today):
        match = re.search(pattern, normalised)
        if match:
            start, end = window()
            return match.group(0).strip(), start, end
    return None


def _amount(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    numbers = spelled_numbers(raw)
    # The whole phrase has to be the number — "ثلاثه" yes, "البند ثلاثه" no.
    if len(numbers) == 1 and numbers[0].start == 0 and numbers[0].end >= len(normalize(raw)):
        return numbers[0].value
    return None


def _back(today: date, unit: str, count: int) -> date:
    if unit == "day":
        return today - timedelta(days=count)
    if unit == "week":
        return today - timedelta(days=7 * count)
    if unit == "month":
        return _shift_months(today, -count)
    return _shift_years(today, -count)


def _counted(normalised: str, today: date) -> tuple[str, date, date] | None:
    """"قبل ٣ أيام", "منذ أسبوعين", "آخر ثلاثين يومًا", "past 10 days"."""
    counted = rf"{_COUNT}\s+({_UNIT_WORD}){_R}{_FROM_EVENT}"
    dual = rf"({_DUAL_WORD}){_R}{_FROM_EVENT}"
    en_counted = rf"{_EN_COUNT}\s+({_UNIT_WORD})\b"

    forms = [
        # one day, week or month that far back
        ("ago", rf"{_L}قبل\s+{counted}"),
        ("ago_dual", rf"{_L}قبل\s+{dual}"),
        ("ago", rf"\b{en_counted}\s+ago\b"),
        # a span from then until today — "منذ" only, because a bare "من" is far more
        # often "more than thirty days" than "since thirty days"
        ("span", rf"{_L}منذ\s+{counted}"),
        ("span_dual", rf"{_L}منذ\s+{dual}"),
        ("span", rf"{_L}(?:ال)?اخر\s+{counted}"),
        ("span_dual", rf"{_L}(?:ال)?اخر\s+{dual}"),
        ("span", rf"\b(?:past|last)\s+{en_counted}{_FROM_EVENT}"),
    ]
    for kind, pattern in forms:
        match = re.search(pattern, normalised)
        if not match:
            continue
        if kind.endswith("dual"):
            unit, count = _DUALS[match.group(1)], 2
        else:
            count = _amount(match.group(1))
            unit = _UNITS[match.group(2)]
            if count is None:
                continue
        if not 0 < count <= 3650:
            continue
        then = _back(today, unit, count)
        phrase = match.group(0).strip()
        if kind.startswith("ago"):
            if unit == "week":
                return (phrase, *_week_of(then))
            if unit == "month":
                return (phrase, *_month_of(then))
            if unit == "year":
                return (phrase, *_year_of(then.year))
            return phrase, then, then
        return phrase, then, today
    return None


def read_relative(question: str, today: date | None = None) -> DateWindow | None:
    """The window of dates a question refers to relatively, or None.

    An explicit date in the question is left to the caller, which gives it precedence:
    someone who wrote "15/08/2026" has said which day they mean.
    """
    today = today or date.today()
    normalised = normalize(question)
    found = _counted(normalised, today) or _fixed(normalised, today)
    if found is None:
        return None
    phrase, start, end = found
    if start > end:
        start, end = end, start
    return DateWindow(phrase=phrase, start=start, end=end, today=today)
