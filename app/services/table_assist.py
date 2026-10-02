"""Tables from scanned PDFs, read by RAGFlow and checked against our own reading.

A scanned page reaches us through Tesseract, which reads Arabic well and tables badly:
it reads a table row by row as loose lines, so a bill of quantities comes back with the
quantity of one item next to the rate of another. RAGFlow's layout reader rebuilds the
same table cell by cell — but it cannot read Arabic at all, scanned or not, and returns
letters in reverse or in no script at all.

So each side keeps what it is good at. Our reading stays the document. For a scanned PDF,
the file is also sent to RAGFlow, and only its tables come back — and only the tables
whose numbers our own reading also found. Digits are the one thing both readers agree
on regardless of script, so a table whose numbers we cannot find in our own text is a
table RAGFlow misread, and it is dropped rather than trusted.

Never fatal. RAGFlow down, slow or wrong means the document is indexed exactly as it
would have been without this.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from app.core.domain import FileType, ParsedDocument, Section, SourceLocation

logger = logging.getLogger(__name__)

#: A number long enough to mean something: an amount, a quantity, a rate, an item code.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
MIN_DIGITS = 3
#: Share of a table's numbers our own reading must also contain. Low on purpose: it only
#: confirms the table belongs to this file. The tables worth adding are exactly the ones
#: our reading got wrong, so a high bar would throw away the help where it is needed.
#: Measured on a scanned bill of quantities: its six tables agreed at 36–75 %.
MIN_AGREEMENT = 0.3
MIN_NUMBERS = 3
#: Arabic that RAGFlow misreads comes back as Latin letters, mostly l, i and j —
#: "lhll Jipleii ilgiipllaili". English tables measured 10–15 % of those letters; misread
#: Arabic measured 63 %. Above this share, the letters are not English.
MAX_THIN_LETTERS = 0.30
MIN_LETTERS_TO_JUDGE = 40


@dataclass(slots=True)
class AssistedTable:
    page: int | None
    markdown: str
    numbers: int
    agreement: float


class _TableReader(HTMLParser):
    """Rows of cell text from RAGFlow's <table> markup. Spans are flattened: a merged
    cell appears once, which keeps every value next to the label it was printed beside."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_table_to_markdown(html: str) -> str:
    reader = _TableReader()
    reader.feed(html)
    rows = reader.rows
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [[cell.replace("|", "/") for cell in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(padded[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join(lines)


def _numbers(text: str) -> set[str]:
    found = set()
    for token in NUMBER.findall(text):
        value = token.replace(",", "")
        value = re.sub(r"\.0+$", "", value)
        if sum(ch.isdigit() for ch in value) >= MIN_DIGITS:
            found.add(value)
    return found


def misread_arabic(text: str) -> bool:
    """Latin letters that are really Arabic read by an English-only reader."""
    letters = re.findall(r"[A-Za-z]", text)
    if len(letters) < MIN_LETTERS_TO_JUDGE:
        return False
    return sum(c in "lijIJ" for c in letters) / len(letters) > MAX_THIN_LETTERS


def agreement(table_text: str, our_text: str) -> tuple[int, float]:
    """How many numbers the table holds, and what share of them our reading also has."""
    theirs = _numbers(table_text)
    if not theirs:
        return 0, 0.0
    ours = _numbers(our_text)
    return len(theirs), len(theirs & ours) / len(theirs)


def select(chunks: list[dict], our_text: str, filename: str = "") -> list[AssistedTable]:
    """The tables among RAGFlow's chunks that can be trusted, in reading order."""
    kept: list[AssistedTable] = []
    for chunk in chunks:
        html = chunk.get("content") or ""
        if "<table" not in html:
            continue
        markdown = html_table_to_markdown(html)
        if misread_arabic(markdown):
            logger.info("Table assist dropped a misread table from %s", filename)
            continue
        count, share = agreement(markdown, our_text)
        if count < MIN_NUMBERS or share < MIN_AGREEMENT:
            logger.info("Table assist dropped a table from %s (%s numbers, %.0f%% agreed)",
                        filename, count, share * 100)
            continue
        positions = chunk.get("positions") or []
        page = int(positions[0][0]) if positions and positions[0] else None
        kept.append(AssistedTable(page=page, markdown=markdown, numbers=count, agreement=share))
    return kept


class RagflowTableAssist:
    def __init__(self, base_url: str, api_key: str, dataset: str = "table_assist",
                 timeout: float = 1800.0, enabled: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dataset = dataset
        self.timeout = timeout
        self.enabled = enabled and bool(base_url and api_key)

    # ------------------------------------------------------------------ pipeline

    def wanted(self, parsed: ParsedDocument) -> bool:
        return (
            self.enabled
            and str(parsed.file_type) == str(FileType.PDF)
            and bool(parsed.metadata.get("ocr_pages"))
        )

    def enrich(self, parsed: ParsedDocument, content: bytes, filename: str) -> ParsedDocument:
        """Add RAGFlow's tables to a scanned PDF. Returns the document unchanged on any failure."""
        if not self.wanted(parsed):
            return parsed
        try:
            tables = self.tables(content, filename, parsed.raw_text)
        except Exception as exc:  # noqa: BLE001 - an optional helper must never fail ingestion
            logger.warning("Table assist skipped for %s: %s", filename, exc)
            parsed.metadata["assisted_tables"] = 0
            return parsed
        for index, table in enumerate(tables, start=1):
            where = f"صفحة {table.page}" if table.page else f"جدول {index}"
            parsed.sections.append(Section(
                heading=f"جدول — {where}",
                level=2,
                content=table.markdown,
                path=[f"جدول — {where}"],
                has_table=True,
                location=SourceLocation(page=table.page, table_index=index),
            ))
        parsed.metadata["assisted_tables"] = len(tables)
        logger.info("Table assist added %s table(s) to %s", len(tables), filename)
        return parsed

    # ------------------------------------------------------------------ RAGFlow

    def tables(self, content: bytes, filename: str, our_text: str) -> list[AssistedTable]:
        with httpx.Client(base_url=f"{self.base_url}/api/v1", timeout=120,
                          headers={"Authorization": f"Bearer {self.api_key}"}) as client:
            dataset = self._dataset(client)
            uploaded = self._ok(client.post(f"/datasets/{dataset}/documents",
                                            files={"file": (filename, content, "application/pdf")}))
            doc_id = uploaded[0]["id"]
            try:
                self._ok(client.post(f"/datasets/{dataset}/documents/parse", json={"document_ids": [doc_id]}))
                self._wait(client, dataset, doc_id)
                chunks = self._chunks(client, dataset, doc_id)
            finally:
                # Nothing stays in RAGFlow: it was asked to read, not to keep.
                try:
                    client.request("DELETE", f"/datasets/{dataset}/documents", json={"ids": [doc_id]})
                except Exception:  # noqa: BLE001
                    logger.warning("Could not remove %s from RAGFlow", filename)
        return select(chunks, our_text, filename)

    @staticmethod
    def _ok(response: httpx.Response):
        body = response.json()
        if response.status_code != 200 or body.get("code", 0) != 0:
            raise RuntimeError(f"RAGFlow: {body.get('message', response.status_code)}")
        return body.get("data")

    def _dataset(self, client: httpx.Client) -> str:
        # Listed and matched here: asked for a name that does not exist yet, RAGFlow
        # answers "lacks permission" rather than an empty list.
        page = 1
        while True:
            listed = self._ok(client.get("/datasets", params={"page": page, "page_size": 100})) or []
            match = next((d for d in listed if d.get("name") == self.dataset), None)
            if match:
                return match["id"]
            if len(listed) < 100:
                break
            page += 1
        return self._ok(client.post("/datasets", json={"name": self.dataset, "chunk_method": "naive"}))["id"]

    def _wait(self, client: httpx.Client, dataset: str, doc_id: str) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            page = 1
            while True:
                data = self._ok(client.get(f"/datasets/{dataset}/documents", params={"page": page, "page_size": 100}))
                docs = data.get("docs", []) if isinstance(data, dict) else data
                match = next((d for d in docs if d.get("id") == doc_id), None)
                if match or len(docs) < 100:
                    break
                page += 1
            run = str((match or {}).get("run", "")).upper()
            if run == "DONE":
                return
            if run in ("FAIL", "CANCEL"):
                raise RuntimeError(f"RAGFlow could not read the file ({run})")
            time.sleep(10)
        raise TimeoutError("RAGFlow took too long to read the file")

    def _chunks(self, client: httpx.Client, dataset: str, doc_id: str) -> list[dict]:
        found: list[dict] = []
        page = 1
        while True:
            data = self._ok(client.get(f"/datasets/{dataset}/documents/{doc_id}/chunks",
                                       params={"page": page, "page_size": 100}))
            items = data.get("chunks", []) if isinstance(data, dict) else (data or [])
            found.extend(items)
            if len(items) < 100:
                return found
            page += 1
