"""Deterministic entity and fact extraction.

Every value produced here is a verbatim substring of the document, located by pattern.
No model is involved, so no entity can be invented — which is exactly what the
grounding requirement demands.
"""

from __future__ import annotations

import re

from app.core.domain import Entity, Section
from app.core.text import normalize

AR = r"؀-ۿ"

DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b|\b\d{4}-\d{1,2}-\d{1,2}\b")
PERCENT = re.compile(r"\b\d{1,3}(?:[.,]\d+)?\s*%")
AMOUNT = re.compile(
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*(?:درهم|AED|dirham|د\.إ)?"
    r"|\b\d+(?:\.\d+)?\s*(?:درهم|AED|dirham)\b"
)
DURATION = re.compile(
    rf"\b\d+(?:\.\d+)?\s*(?:يوم[{AR}]*|أيام|شهر[{AR}]*|أشهر|سنة|سنوات|"
    r"days?|months?|years?|weeks?|أسابيع|أسبوع)\b"
)
CLAUSE = re.compile(
    r"(?:البند|المادة|الفقرة)\s*(?:رقم\s*)?[\d]+(?:[./-]\d+)*"
    r"|Sub-?Clause\s*\d+(?:\.\d+)*"
    r"|\bClause\s*\d+(?:\.\d+)*"
)
IDENTIFIER = re.compile(r"\b[A-Za-z0-9]{2,}(?:[-/][A-Za-z0-9]{2,}){1,6}\b")
FORM_CODE = re.compile(rf"(?:نموذج|form)\s+[A-Z0-9][A-Za-z0-9-]{{2,}}|\b[A-Z]{{2,5}}-F-\d{{2,4}}\b")
ORG_AR = re.compile(rf"(?:شركة|مؤسسة|بنك|بلدية|دائرة|هيئة|وزارة)\s+[{AR}][{AR}\s]{{2,45}}?(?=\s*[|،.\n]|\s+ذ\.م\.م|$)")
ORG_EN = re.compile(r"\b[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,}){1,4}\s+(?:Consultancy|Engineering|Contracting|Construction|LLC|Ltd)\b")
PERSON = re.compile(rf"(?:المهندس|المهندسة|م\.|السيد|السيدة|الدكتور|د\.|الخبير|المحامي)\s*/?\s*[{AR}]{{2,}}(?:\s+[{AR}]{{2,}}){{1,4}}")
PLOT = re.compile(rf"(?:القطعة|قطعة|Plot)\s*(?:رقم\s*)?\d+")

TABLE_ROW = re.compile(r"^\s{0,3}\|(?P<label>[^|]+)\|(?P<value>[^|]*)\|?\s*$")
TABLE_SEPARATOR = re.compile(r"^\s{0,3}\|[\s:|-]+\|\s*$")
MARKUP = re.compile(r"[*_`]|^#+\s*")

MAX_CONTEXT = 200
MIN_IDENTIFIER_DIGITS = 1

# Words that cannot start a person's name, so a title followed by one of them is prose.
NOT_A_NAME = frozenset(normalize(w) for w in """
في من على عن الى إلى لدى نحو خلال عبر مع بعد قبل عند حتى ثم او أو لا ما هو هي
لن لم لما كلما بلا دون سوى كان كانت يكون تكون التي الذي الذين هذا هذه ذلك تلك
مخول مخوَّل مكلف مطالب ملزم قال قالت طلب طلبت أرسل ارسل أعلن اعلن ردَّ رد ردت
أزال ازال أكد اكد رفض رفضت وافق أضاف اضاف ذكر ذكرت بيّن بين أوضح اوضح منح منحت
لديه لديها عليه عليها إليه اليه له لها به بها منه منها
""".split())


class EntityExtractor:
    """Pattern sweep over each section, plus label/value pairs from Markdown tables."""

    def extract(self, sections: list[Section]) -> list[Entity]:
        found: list[Entity] = []
        seen: set[tuple[str, str, str]] = set()

        for section in sections:
            for entity in self._from_tables(section) + self._from_patterns(section):
                key = (entity.kind, entity.normalized, entity.section_id)
                if key in seen or not entity.value.strip():
                    continue
                seen.add(key)
                found.append(entity)
        return found

    def _from_tables(self, section: Section) -> list[Entity]:
        """`| label | value |` rows carry the most precise facts in these documents."""
        entities: list[Entity] = []
        for line in section.content.split("\n"):
            if TABLE_SEPARATOR.match(line):
                continue
            match = TABLE_ROW.match(line)
            if not match:
                continue

            label = self._clean(match.group("label"))
            value = self._clean(match.group("value"))
            if not label or not value or len(value) > 300:
                continue
            if normalize(label) in {"البند", "التفاصيل", "القيمة", "item", "value"}:
                continue

            entities.append(
                Entity(
                    kind=self._classify(value),
                    value=value,
                    normalized=normalize(value),
                    section_id=section.section_id,
                    context=f"{label}: {value}"[:MAX_CONTEXT],
                    label=label,
                )
            )
        return entities

    def _from_patterns(self, section: Section) -> list[Entity]:
        entities: list[Entity] = []
        text = section.content

        for kind, pattern in (
            ("date", DATE),
            ("percentage", PERCENT),
            ("amount", AMOUNT),
            ("duration", DURATION),
            ("clause", CLAUSE),
            ("form", FORM_CODE),
            ("organization", ORG_AR),
            ("organization", ORG_EN),
            ("person", PERSON),
            ("location", PLOT),
            ("identifier", IDENTIFIER),
        ):
            for match in pattern.finditer(text):
                value = self._clean(match.group(0))
                if not self._is_useful(kind, value):
                    continue
                entities.append(
                    Entity(
                        kind=kind,
                        value=value,
                        normalized=normalize(value),
                        section_id=section.section_id,
                        context=self._context(text, match.start(), match.end()),
                    )
                )
        return entities

    @staticmethod
    def _is_useful(kind: str, value: str) -> bool:
        if not value or len(value) > 120:
            return False
        if kind == "person":
            # A title followed by a function word or a verb is a sentence, not a name:
            # "الخبير في المحضر", "الخبير كلما ردَّها اعترضت".
            words = value.split()
            if len(words) < 2:
                return False
            return normalize(words[1]) not in NOT_A_NAME
        if kind == "identifier":
            if len(value) < 7 or sum(c.isdigit() for c in value) < MIN_IDENTIFIER_DIGITS:
                return False
            if DATE.fullmatch(value):
                return False
        if kind == "organization" and len(value) < 8:
            return False
        return True

    @staticmethod
    def _classify(value: str) -> str:
        if DATE.fullmatch(value):
            return "date"
        if PERCENT.fullmatch(value):
            return "percentage"
        if AMOUNT.fullmatch(value):
            return "amount"
        if DURATION.fullmatch(value):
            return "duration"
        if IDENTIFIER.fullmatch(value) and any(c.isdigit() for c in value):
            return "identifier"
        if PERSON.match(value):
            return "person"
        if ORG_AR.match(value) or ORG_EN.match(value):
            return "organization"
        return "attribute"

    @staticmethod
    def _clean(text: str) -> str:
        return MARKUP.sub("", text).strip().strip("|").strip()

    @staticmethod
    def _context(text: str, start: int, end: int) -> str:
        left = max(0, start - MAX_CONTEXT // 2)
        right = min(len(text), end + MAX_CONTEXT // 2)
        return re.sub(r"\s+", " ", text[left:right]).strip()
