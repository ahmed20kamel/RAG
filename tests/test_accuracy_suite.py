"""The accuracy suite's judge: a right answer is recognised however its figures are written,
and a question about something no file says is right only when refused.

    python tests/test_accuracy_suite.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from accuracy_suite import judge  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    item = {"kind": "table", "document": "BOQ.pdf", "facts": [["1,250", "1250"], ["90,000"]]}
    check(judge(item, "السعر 1250 والإجمالي 90000 درهم", ["BOQ.pdf"])["ok"], "figures without separators count")
    check(judge(item, "السعر ١٢٥٠ والإجمالي ٩٠,٠٠٠", [])["ok"], "Arabic-Indic digits count")
    missing = judge(item, "السعر 1,250", ["BOQ.pdf"])
    check(not missing["ok"] and missing["missing"] == ["90,000"], "a missing fact is named")
    check(judge(item, "1,250 و 90,000", ["CONTRACT.pdf"])["cited_document"] is False, "a wrong source is noticed")
    check(judge({"kind": "fact", "facts": [["المدعى عليها"]]}, "المدعي عليها هي سافكوم", [])["ok"],
          "spelling variants of the same word count")

    absent = {"kind": "absent", "facts": []}
    check(judge(absent, "لا توجد معلومات كافية في قاعدة المعرفة للإجابة عن هذا السؤال.", [])["ok"],
          "a refusal is right for a question no file answers")
    check(not judge(absent, "قيمة التأمين 50,000 درهم", [])["ok"], "an invented answer is wrong")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
