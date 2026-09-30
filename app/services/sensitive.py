"""Personal and access data an answer does not need, withheld unless asked for.

Asked for a summary of a case file, an answer listed the court-appointed expert's mobile
number, his personal e-mail address, and the passcode of a video hearing — all present
in the file, none asked for. The file is the company's and the reader may be entitled to
it, but an answer is copied, forwarded and pasted, and "لا تكشف إلا ما تتطلبه المهمة" is
the rule the company works by.

So after the answer is written and validated, contact and access details are masked
unless the question asked for that kind of detail. Deterministic — pattern matching, no
model — and narrow: only the kinds below, each recognised by its shape, and each released
by the words a person uses to ask for it. The sources themselves are untouched; a reader
who opens a source still sees the document as it is.

Names are not masked. A name cannot be told from any other capitalised or Arabic word by
its shape, and masking by guesswork would blank the parties an answer is about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.text import normalize

MASK_AR = "[محجوب — اطلبه صراحةً إن احتجته]"
MASK_EN = "[withheld — ask for it explicitly]"


@dataclass(frozen=True)
class Kind:
    name: str
    label: str
    pattern: re.Pattern[str]
    #: Words that mean the question asked for this kind of detail (normalised).
    released_by: tuple[str, ...]
    #: When set, only this group of the match is masked — the label before it stays.
    group: int = 0


_B = r"(?<![\w@.+])"
_E = r"(?![\w@])"

KINDS: tuple[Kind, ...] = (
    Kind(
        "email", "بريد إلكتروني",
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        ("بريد", "ايميل", "إيميل", "الايميل", "email", "e-mail", "mail", "تواصل", "contact"),
    ),
    Kind(
        "phone", "رقم هاتف",
        # UAE mobile and landline, local or international form. Deliberately requires
        # the leading 0 / +971 / 00971, so reference numbers like "260325-7491734" or a
        # contract number are never read as phones.
        re.compile(
            _B + r"(?:\+971|00971|0)\s?(?:5\d|[2-4679])[\s-]?\d{3}[\s-]?\d{4}" + _E
        ),
        ("هاتف", "تلفون", "تليفون", "جوال", "موبايل", "رقم التواصل", "اتصال", "phone",
         "mobile", "telephone", "contact", "واتساب", "whatsapp"),
    ),
    Kind(
        "passcode", "رمز دخول",
        re.compile(
            r"(?i)((?:passcode|password|pass\s*code|pwd|pin|كلمة\s*(?:السر|المرور)|رمز\s*(?:الدخول|المرور|الاجتماع))"
            r"\s*[:：=]?\s*)([A-Za-z0-9@#$%^&*!_\-]{3,40})"
        ),
        ("كلمه السر", "كلمه المرور", "رمز الدخول", "رمز المرور", "passcode", "password",
         "الدخول للاجتماع", "بيانات الاجتماع", "رابط الاجتماع"),
        group=2,
    ),
    Kind(
        "meeting_id", "معرّف اجتماع",
        re.compile(r"(?i)((?:meeting\s*id|zoom\s*id|معرف\s*الاجتماع|رقم\s*الاجتماع)\s*[:：]?\s*)(\d[\d\s]{8,13}\d)"),
        ("معرف الاجتماع", "رقم الاجتماع", "meeting id", "zoom", "بيانات الاجتماع", "رابط الاجتماع"),
        group=2,
    ),
    Kind(
        "emirates_id", "رقم هوية",
        re.compile(_B + r"784[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d" + _E),
        # "هويت" catches "هويته" and "هويتها", which do not contain "هويه".
        ("هويه", "هويت", "الهويه", "رقم الهويه", "اماراتيه", "emirates id", "id number", "identity"),
    ),
    Kind(
        "iban", "رقم حساب بنكي",
        re.compile(_B + r"AE\d{2}\s?(?:\d{4}\s?){4}\d{3}" + _E),
        ("ايبان", "آيبان", "iban", "حساب بنكي", "رقم الحساب", "bank account"),
    ),
)


@dataclass
class Redaction:
    text: str
    #: kind name → how many were masked
    masked: dict[str, int] = field(default_factory=dict)

    def summary(self, english: bool = False) -> str:
        if not self.masked:
            return ""
        labels = {k.name: k.label for k in KINDS}
        if english:
            parts = [f"{n} {k.replace('_', ' ')}" for k, n in self.masked.items()]
            return "Withheld because the question did not ask for them: " + ", ".join(parts)
        parts = [f"{labels[k]} ({n})" for k, n in self.masked.items()]
        return "حُجبت لأن السؤال لم يطلبها: " + "، ".join(parts)


def _asked(question: str, kind: Kind) -> bool:
    folded = normalize(question).lower()
    return any(normalize(cue).lower() in folded for cue in kind.released_by)


def redact(answer: str, question: str, english: bool = False) -> Redaction:
    """The answer with contact and access details masked, unless the question asked."""
    mask = MASK_EN if english else MASK_AR
    text = answer
    masked: dict[str, int] = {}
    for kind in KINDS:
        if _asked(question, kind):
            continue

        def replace(match: re.Match, kind: Kind = kind) -> str:
            masked[kind.name] = masked.get(kind.name, 0) + 1
            if kind.group:
                return match.group(1) + mask
            return mask

        text = kind.pattern.sub(replace, text)
    return Redaction(text=text, masked=masked)
