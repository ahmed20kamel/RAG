"""Extractive section summaries.

Built from sentences and table labels taken verbatim from the section, so a summary
can never introduce a fact the section does not contain. Used to improve retrieval
only — answers are always generated from the original chunk text.
"""

from __future__ import annotations

import re

from app.core.domain import Section

TABLE_ROW = re.compile(r"^\s{0,3}\|(?P<label>[^|]+)\|")
TABLE_SEPARATOR = re.compile(r"^\s{0,3}\|[\s:|-]+\|\s*$")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟])\s+")
MARKUP = re.compile(r"[*_`>]|^#+\s*")

MAX_SUMMARY_CHARS = 400
MAX_LABELS = 14


class SectionSummarizer:
    def summarize(self, section: Section) -> str:
        parts: list[str] = []
        labels = self._table_labels(section.content)
        if labels:
            parts.append("يغطي: " + "، ".join(labels))

        prose = self._leading_prose(section.content, budget=MAX_SUMMARY_CHARS - len(parts[0]) if parts else MAX_SUMMARY_CHARS)
        if prose:
            parts.append(prose)

        if not parts:
            parts.append(", ".join(section.terms[:8]))

        summary = f"{section.breadcrumb} — " + " | ".join(p for p in parts if p)
        return summary[:MAX_SUMMARY_CHARS].strip()

    def apply(self, sections: list[Section]) -> None:
        for section in sections:
            section.summary = self.summarize(section)

    @staticmethod
    def _table_labels(content: str) -> list[str]:
        labels: list[str] = []
        for line in content.split("\n"):
            if TABLE_SEPARATOR.match(line):
                continue
            match = TABLE_ROW.match(line)
            if not match:
                continue
            label = MARKUP.sub("", match.group("label")).strip()
            if label and label not in labels and len(label) < 60:
                labels.append(label)
        skip = {"البند", "التفاصيل", "القيمة", "item", "value", "التاريخ", "الواقعة"}
        return [lbl for lbl in labels if lbl not in skip][:MAX_LABELS]

    @staticmethod
    def _leading_prose(content: str, budget: int) -> str:
        if budget <= 0:
            return ""
        lines = [
            MARKUP.sub("", line).strip()
            for line in content.split("\n")
            if line.strip() and not line.lstrip().startswith(("|", "```", "~~~"))
        ]
        if not lines:
            return ""
        sentences = SENTENCE_SPLIT.split(" ".join(lines))
        collected: list[str] = []
        used = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if used + len(sentence) > budget and collected:
                break
            collected.append(sentence)
            used += len(sentence)
            if len(collected) >= 3:
                break
        return " ".join(collected)
