"""Telling "what does the policy say" apart from "what is the balance right now".

This system holds documents. Documents are written once and filed; the figures inside
them were true when somebody wrote them down. An operational system holds values that
changed an hour ago. Asked for a current balance, a stock level or the status of a
supplier, a retrieval system will answer from whatever the index happens to hold and
will sound exactly as certain as it does when quoting a contract clause. Confident and
wrong is worse than a refusal, and it is the specific failure this integration is most
likely to produce.

So when an answer could not be grounded, the question is checked against a fixed list of
markers and the caller is told plainly that this looks like a question for the
operational system. Two properties matter:

*Deterministic.* A list of substrings, no model. A classifier that is right most of the
time is not a safety control — the whole point is that the same question is always
routed the same way, and that the routing can be read off the source rather than
inferred from behaviour.

*Advisory, and only on a refusal.* It never suppresses an answer that was grounded, and
it never routes anything by itself. It sets one boolean on a reply the system was going
to refuse anyway, which the orchestrating agent is free to ignore. That is why a false
positive here costs nothing: the alternative to the hint was silence, not an answer.
"""

from __future__ import annotations

import re

from app.core.text import normalize

#: Matched against the normalised question. Written in normalised form already —
#: alef and ya folded, ta marbuta flattened, diacritics gone — because that is what
#: they are compared against.
LIVE_DATA_MARKERS: tuple[str, ...] = (
    # Arabic — currency of the value
    "حاليا",
    "الحالي",
    "الحاليه",
    "الان",
    "هذه اللحظه",
    "حتى الان",
    "اخر تحديث",
    "المحدث",
    # Arabic — operational quantities
    "الرصيد",
    "الارصده",
    "المخزون",
    "المتوفر",
    "المتبقي",
    "الكميه المتاحه",
    "حاله المورد",
    "حاله الموظف",
    "حاله الطلب",
    "حاله امر الشراء",
    "قيد التنفيذ",
    "قيد الاعتماد",
    "المعلقه",
    "غير المسدده",
    "المستحقه",
    # Arabic — periods that only an operational system can resolve
    "هذا الشهر",
    "هذا الاسبوع",
    "هذه السنه",
    "اليوم",
    "امس",
    "الشهر الحالي",
    "السنه الحاليه",
    # Arabic — counting and totalling live records
    "كم عدد",
    "كم بلغ",
    "اجمالي المشتريات",
    "اجمالي المصروفات",
    "عدد اوامر الشراء",
    "عدد الطلبات",
    # English
    "current",
    "currently",
    "right now",
    "as of today",
    "latest",
    "up to date",
    "balance",
    "inventory",
    "stock level",
    "in stock",
    "outstanding",
    "pending",
    "overdue",
    "this month",
    "this week",
    "today",
    "how many",
    "total purchases",
    "open orders",
    "purchase orders",
)


#: Letters either side of a marker that mean it is part of a longer word rather than a
#: word of its own. Arabic needs this stated explicitly: `\b` in Python's `re` works on
#: word characters, which is not enough here — the first version of this matched "الآن"
#: inside "الانصراف" and reported a question about the attendance policy as a request
#: for live data. Short Arabic particles are dense with such overlaps, so the match is
#: anchored at both ends.
_LETTER = r"[\w؀-ۿ]"
_MARKER_RE = re.compile(
    "|".join(
        f"(?<!{_LETTER}){re.escape(marker)}(?!{_LETTER})"
        # Longest first, so a compound marker is preferred over a fragment of itself.
        for marker in sorted(LIVE_DATA_MARKERS, key=len, reverse=True)
    )
)


def needs_live_data(question: str) -> bool:
    """Whether this reads like a question for the operational system.

    Called only when an answer could not be grounded. On a grounded answer the question
    has already been answered from evidence, and second-guessing that with a word list
    would be the list overruling the documents.
    """
    if not question:
        return False
    return bool(_MARKER_RE.search(normalize(question)))


def matched_markers(question: str) -> list[str]:
    """Which markers fired. For diagnosing a wrong answer to "why was this flagged?"."""
    return _MARKER_RE.findall(normalize(question)) if question else []


def suggested_source(question: str) -> str | None:
    """`"erp"` when the question looks operational, otherwise nothing.

    A hint, never an instruction. Which system owns which question is the orchestrating
    agent's decision, and it holds information this one does not.
    """
    return "erp" if needs_live_data(question) else None
