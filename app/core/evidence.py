"""One view over everything retrieval produced, with provenance on every item.

Chunks, extracted facts and dated entries arrive from different places and in different
shapes. Coverage checking needs them as one addressable set, deduplicated, each item
still pointing at the citation it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.text import normalize


@dataclass(slots=True)
class EvidenceItem:
    """A single value the sources state, and where it is stated."""

    kind: str
    value: str
    label: str = ""
    citation: int = 0
    document_id: str = ""
    section_id: str = ""
    section_heading: str = ""
    primary: bool = False
    confidence: float = 0.0

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        return (self.kind, normalize(self.label), normalize(self.value))

    def describe(self) -> str:
        head = f"{self.label}: {self.value}" if self.label else self.value
        return f"{head}  [{self.citation}]"


@dataclass(slots=True)
class EvidenceSet:
    """Every item the answer may draw on, plus the raw context it came from."""

    items: list[EvidenceItem] = field(default_factory=list)
    context: str = ""

    def add(self, item: EvidenceItem) -> bool:
        """Add unless an identical value from the same kind is already present."""
        if not item.value.strip():
            return False
        key = item.dedupe_key
        if any(existing.dedupe_key == key for existing in self.items):
            return False
        self.items.append(item)
        return True

    def of_kinds(self, kinds: tuple[str, ...]) -> list[EvidenceItem]:
        return [item for item in self.items if item.kind in kinds]

    def __len__(self) -> int:
        return len(self.items)
