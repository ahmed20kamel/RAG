"""Greetings and thanks are answered as what they are, never searched as questions.

Measured on Rag-pc: «مرحبا» was read as a two-word question and answered "your question
is short and fits several files — which one?".

    python tests/test_small_talk.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.rag_service import GREETING, SMALL_TALK  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    for greeting in ("مرحبا", "مرحباً", "اهلا، كيف حالك؟", "صباح الخير", "هلا", "hi", "Hello!", "hey 👋"):
        check(bool(GREETING.match(greeting)), f"a greeting: {greeting}")
    for salam in ("السلام عليكم", "السلام عليكم ورحمة الله وبركاته", "سلام"):
        match = GREETING.match(salam)
        check(bool(match and match.group("salam")), f"a salam, answered with its reply: {salam}")
    for question in ("مرحبا ما قيمة العقد؟", "اهلا بالتعديل الجديد في العقد", "hello world contract value"):
        check(not GREETING.match(question), f"a question that opens with a greeting is a question: {question}")
    for thanks in ("شكرا", "تمام", "thanks"):
        check(bool(SMALL_TALK.match(thanks)) and not GREETING.match(thanks), f"thanks stay thanks: {thanks}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
