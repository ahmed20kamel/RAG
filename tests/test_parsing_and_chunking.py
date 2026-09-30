"""Offline checks for the parser and chunker. Run: python -m tests.test_parsing_and_chunking"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parsers.markdown_parser import MarkdownParser  # noqa: E402
from app.services.chunking import HeadingAwareChunker  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(label)
    print(f"[{status}] {label}")


def run() -> None:
    parser = MarkdownParser()
    chunker = HeadingAwareChunker(chunk_size=400, chunk_overlap=60, min_chunk_size=80)

    source = """---
title: Test Doc
category: QA/QC
version: 3.4
---

# Main

Intro paragraph.

## Section A

Body of A.

```python
# this heading marker must not create a section
def f():
    return 1
```

## Section B

| Col | Val |
|---|---|
| a | 1 |
| b | 2 |

### Sub B.1

Deeper content.
"""
    parsed = parser.parse(source.encode("utf-8"), "test.md")

    check(parsed.title == "Test Doc", "front matter title wins over H1")
    check(parsed.metadata.get("category") == "QA/QC", "front matter category parsed")
    check(str(parsed.metadata.get("version")) == "3.4", "front matter version parsed")
    check(parsed.language == "en", "language detected as English")

    headings = [s.heading for s in parsed.sections]
    check("Section A" in headings and "Section B" in headings, "H2 sections detected")
    check("this heading marker must not create a section" not in headings, "code fence not parsed as heading")

    sub = next((s for s in parsed.sections if s.heading == "Sub B.1"), None)
    check(sub is not None and sub.path == ["Main", "Section B", "Sub B.1"], "heading path built correctly")

    chunks = chunker.chunk(parsed, "doc-1", "test.md")
    check(bool(chunks), "chunks produced")
    check(all(c.section for c in chunks), "every chunk keeps its section breadcrumb")
    check(all(c.embed_text.startswith(parsed.title) for c in chunks), "embed text carries title context")

    code_chunk = next((c for c in chunks if "def f():" in c.content), None)
    check(code_chunk is not None and code_chunk.content.count("```") == 2, "code block kept intact")

    table_chunk = next((c for c in chunks if "| Col | Val |" in c.content), None)
    check(table_chunk is not None and "| b | 2 |" in table_chunk.content, "table kept intact")

    # Oversized section must split without exceeding the limit by much.
    big = "# Big\n\n" + "\n\n".join(f"فقرة رقم {i} تحتوي على نص طويل نسبيًا للاختبار." * 3 for i in range(40))
    big_parsed = parser.parse(big.encode("utf-8"), "big.md")
    big_chunks = chunker.chunk(big_parsed, "doc-2", "big.md")
    check(len(big_chunks) > 1, "oversized section is split")
    check(max(c.char_count for c in big_chunks) <= 400 * 1.6, "split chunks respect the size budget")
    check(big_parsed.language == "ar", "Arabic language detected")

    setext = parser.parse(b"Title Here\n==========\n\nSome body text.\n", "setext.md")
    check(setext.sections[0].heading == "Title Here", "setext heading supported")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    run()
