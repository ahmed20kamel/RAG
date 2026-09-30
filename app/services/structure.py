"""Structure analysis: section identity, parent/child links, content flags, key terms.

Runs once per document at ingestion. Everything here is derived deterministically from
the text — nothing is generated, so nothing can be invented.
"""

from __future__ import annotations

import re
from collections import Counter

from app.core.domain import DocumentType, ParsedDocument, Section
from app.core.text import IDENTIFIER, content_terms, normalize

TABLE_ROW = re.compile(r"^\s{0,3}\|", re.MULTILINE)
LIST_ITEM = re.compile(r"^\s{0,3}([-*+]|\d{1,3}[.)])\s+", re.MULTILINE)
CODE_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})", re.MULTILINE)

TYPE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (DocumentType.LEGAL_CASE, ("دعوى", "المدعي", "المدعى عليه", "المحكمة", "جلسة", "قضية", "حكم", "خبير منتدب")),
    (DocumentType.METHOD_STATEMENT, ("method statement", "permit to work", "hse", "excavation", "شروط السلامة")),
    (DocumentType.CONTRACT, ("عقد", "بنود العقد", "sub-clause", "fidic", "contract", "المقاول", "صاحب العمل")),
    (DocumentType.PROCEDURE, ("إجراء", "إجراءات", "procedure", "خطوات العمل")),
    (DocumentType.REPORT, ("تقرير", "report", "findings", "النتائج")),
)

MAX_TERMS_PER_SECTION = 12


class DocumentStructureAnalyzer:
    """Turns a flat section list into an addressable, linked section tree."""

    def analyze(self, parsed: ParsedDocument) -> ParsedDocument:
        self._assign_ids_and_links(parsed.sections)
        for section in parsed.sections:
            self._describe(section)
        parsed.document_type = self._detect_type(parsed)
        return parsed

    @staticmethod
    def _assign_ids_and_links(sections: list[Section]) -> None:
        by_depth: dict[int, str] = {}
        index: dict[str, Section] = {}

        for order, section in enumerate(sections):
            section.order = order
            section.section_id = f"s{order:04d}"
            section.child_ids = []
            index[section.section_id] = section

            depth = max(len(section.path), 1)
            parent_id = by_depth.get(depth - 1)
            section.parent_id = parent_id
            if parent_id and parent_id in index:
                index[parent_id].child_ids.append(section.section_id)

            by_depth[depth] = section.section_id
            for deeper in [d for d in by_depth if d > depth]:
                by_depth.pop(deeper)

    @staticmethod
    def _describe(section: Section) -> None:
        content = section.content
        section.has_table = bool(TABLE_ROW.search(content))
        section.has_list = bool(LIST_ITEM.search(content))
        section.has_code = bool(CODE_FENCE.search(content))

        counts = Counter(term for term in content_terms(content) if len(term) > 2)
        counts.update(IDENTIFIER.findall(normalize(content)))
        heading_terms = [t for t in content_terms(section.heading) if len(t) > 2]
        ranked = [term for term, _ in counts.most_common(MAX_TERMS_PER_SECTION * 2)]
        merged = list(dict.fromkeys(heading_terms + ranked))
        section.terms = merged[:MAX_TERMS_PER_SECTION]

    @staticmethod
    def _detect_type(parsed: ParsedDocument) -> str:
        declared = str(parsed.metadata.get("document_type", "") or "").strip().lower()
        if declared in set(DocumentType):
            return declared

        haystack = normalize(
            " ".join([parsed.title, *(s.heading for s in parsed.sections)])
            + " "
            + parsed.raw_text[:6000]
        )
        best_type, best_score = DocumentType.GENERAL, 0
        for doc_type, hints in TYPE_HINTS:
            score = sum(1 for hint in hints if normalize(hint) in haystack)
            if score > best_score:
                best_type, best_score = doc_type, score
        return best_type if best_score >= 2 else DocumentType.GENERAL
