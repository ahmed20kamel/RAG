"""A question that names a project finds that project's files; one that names none, none.

Measured on the company's files: Majed Al Hammadi's revised BOQ never names its owner,
and an Arabic question about "فيلا ماجد الحمادي" was answered from another villa's BOQ.

    python tests/test_project_scope.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from app.services.project_scope import ProjectScope  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        if detail:
            print(f"        {detail}")
        FAILURES.append(label)


CONTRACT_TEXT = "مدة تنفيذ المشروع ثمانية عشر شهرا من تاريخ الترسية وعلى المقاول تنفيذ الأعمال"
ROWS = [
    ("m1", "ACTUAL REVISED BOQ 25-8-2025.xlsx", "Majed Al Hammadi", [], "Beam 72 1250 90000 " + CONTRACT_TEXT),
    ("m2", "ملحق العقد 2 ماجد الحمادى.docx", "Majed Al Hammadi", ["ماجد عيسى حسن احمد الحمادي"], CONTRACT_TEXT),
    ("a1", "BOQ - ASMA MOHAMED ALNUAIMI.xlsm", "Private Villa - MBZ", ["Asma Mohamed Alnuaimi"], CONTRACT_TEXT),
    ("s1", "TILES_Sultan Al Hajeri.pdf", "Sultan Al Hajeri", [], "porcelain tiles " + CONTRACT_TEXT),
    ("c1", "مذكرة دفاع 1 سافيكو .pdf", "قضية 130/2026", ["سافكوم للمقاولات"], "سافكوم المدعى عليها " + CONTRACT_TEXT),
    ("c2", "صحيفة الدعوى.pdf", "قضية 130/2026", [], "المدعى عليها سافكوم " + CONTRACT_TEXT),
    ("x1", "EID.pdf", "", [], "identity"),
]


def main() -> int:
    scope = ProjectScope(loader=lambda: ROWS, refresh_seconds=3600)

    documents, projects = scope.resolve("كم مدة تنفيذ مشروع فيلا ماجد الحمادي في جدول الكميات المعدل؟")
    check(projects == ["Majed Al Hammadi"] and set(documents) == {"m1", "m2"},
          "an owner named in Arabic finds his project, including a file that never names him", str(projects))
    documents, projects = scope.resolve("In the Majed Al Hammadi revised BOQ, what is the beam rate?")
    check(projects == ["Majed Al Hammadi"], "and named in English", str(projects))
    documents, projects = scope.resolve("ما أهم دفوع سافكوم في مذكرتها؟")
    check(projects == ["قضية 130/2026"] and set(documents) == {"c1", "c2"}, "a party names its case", str(projects))
    documents, projects = scope.resolve("ما مدة تنفيذ المشروع حسب العقد؟")
    check(not documents, "a question that names no project is not narrowed", str(projects))
    check(scope.project("a1") == "Private Villa - MBZ" and scope.project("x1") == "",
          "every source knows its project; a file without one has none")
    documents, _ = scope.resolve("ما سعر البلاط في مشروع سلطان الهاجري؟", among=["a1", "m1"])
    check(documents == [], "only files the reader may see are offered")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
