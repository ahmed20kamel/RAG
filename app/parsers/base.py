"""Parser abstraction. Phase 2 formats (PDF/DOCX/XLSX) plug in here without touching services."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.domain import ParsedDocument


class DocumentParser(ABC):
    """Turns raw bytes of one supported format into a ParsedDocument."""

    name: str = "base"
    supported_extensions: tuple[str, ...] = ()

    def supports(self, extension: str) -> bool:
        return extension.lower() in self.supported_extensions

    @abstractmethod
    def parse(self, content: bytes, filename: str) -> ParsedDocument:
        """Parse raw bytes. Must raise ParsingError on unrecoverable input."""


class ParserRegistry:
    """Resolves the parser responsible for a file extension."""

    def __init__(self, parsers: list[DocumentParser] | None = None) -> None:
        self._parsers: list[DocumentParser] = list(parsers or [])

    def register(self, parser: DocumentParser) -> None:
        self._parsers.append(parser)

    def get(self, extension: str) -> DocumentParser | None:
        return next((p for p in self._parsers if p.supports(extension)), None)

    @property
    def supported_extensions(self) -> list[str]:
        return sorted({ext for p in self._parsers for ext in p.supported_extensions})

    def effective_extensions(self, allowed: list[str] | None) -> list[str]:
        """What may actually be uploaded: a parser exists, and an operator permits it.

        Two lists used to decide this independently — the registry here and
        `allowed_extensions` in settings — and they drifted the moment a parser was
        registered without the settings list being updated too. The upload endpoint
        refused `.xlsx` while `/api/config` advertised it, so the interface offered a
        file type the server would reject.

        Asking both questions in one place fixes that by construction. Registering a
        parser is what makes a format possible; the setting can only narrow that, never
        widen it, because a permitted extension with no parser behind it is a promise
        nothing can keep.
        """
        supported = set(self.supported_extensions)
        if not allowed:
            return sorted(supported)
        return sorted(supported & {ext.lower() for ext in allowed})
