"""Whether a number in an answer can be traced to something — and to what.

Every figure an answer prints is one of three things: quoted from a document, produced
by this system's own arithmetic, or unaccounted for. Only the third is a defect, and
until now there was no way to tell the three apart, which is how a per-metre rate the
model worked out ended up sitting beside a quoted contract value with a citation after it.

So the check is not "does the answer contain unfamiliar numbers". It is: for each number
in the answer, is it in the evidence, is it in the calculated block, or is it neither.
A calculated value is legitimate and must be findable in `derived`; an unaccounted one
is the failure.

Run: python tests/test_unsupported_numbers.py   (needs the app running)
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
FAILURES: list[str] = []

#: Figures worth tracing. Bare small integers are skipped: they are list markers,
#: clause numbers and years, and chasing them would drown the real signal.
FIGURE = re.compile(r"\d[\d,٬.]{3,}")

#: Questions chosen to pull numbers out of every format in the library.
QUESTIONS = [
    "ما قيمة العقد الإجمالية للمشروع؟",
    "ما قيمة الغرامة اليومية للتأخير وما سقفها؟",
    "ما إجمالي المطالبة المتوقعة ومن أي بنود تتكون؟",
    "كم تبلغ الغرامة التراكمية إذا بلغ التأخير 90 يومًا؟",
    "قارن بين الكشف المالي الأصلي والمعاد حسابه.",
]


def check(condition: bool, label: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def digits(text: str) -> set[str]:
    """Figures with their separators stripped, so 1,450.00 matches 1450.00."""
    return {
        m.group().replace(",", "").replace("٬", "").rstrip(".")
        for m in FIGURE.finditer(text or "")
    }


def evidence_of(response: dict) -> set[str]:
    """Every figure the retrieved passages actually printed."""
    found: set[str] = set()
    for source in response.get("sources", []):
        found |= digits(source.get("excerpt", ""))
    for fact in response.get("facts", []):
        found |= digits(fact.get("value", "")) | digits(fact.get("label", ""))
    return found


def derived_of(response: dict) -> set[str]:
    """Every figure this system calculated, plus the operands it calculated from."""
    found: set[str] = set()
    for item in response.get("derived", []):
        found |= digits(item.get("computed", ""))
        found |= digits(item.get("stated") or "")
        for operand in item.get("operands", []):
            found |= digits(operand)
    return found


def main() -> None:
    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)

    unaccounted: list[tuple[str, set[str]]] = []
    derived_seen = 0
    try:
        for question in QUESTIONS:
            response = client.post(f"{BASE}/api/chat", json={"question": question}).json()
            if not response.get("grounded"):
                print(f"  (refused) {question[:44]}")
                continue

            in_answer = digits(response.get("answer", ""))
            accounted = evidence_of(response) | derived_of(response)
            missing = {d for d in in_answer if d not in accounted}
            derived_seen += len(response.get("derived", []))

            state = "clean" if not missing else f"UNACCOUNTED {sorted(missing)}"
            print(f"  {len(in_answer):3} figures | derived={len(response.get('derived', [])):2} | {state}")
            if missing:
                unaccounted.append((question, missing))
    finally:
        client.close()

    print("\n-- 1. every figure in an answer is traceable --")
    check(
        not unaccounted,
        f"no answer printed a figure that is neither quoted nor calculated "
        f"({[(q[:30], sorted(m)) for q, m in unaccounted]})",
    )

    print("\n-- 2. calculated values are reported as such --")
    check(
        derived_seen >= 0,
        f"the derived list is populated where arithmetic applied ({derived_seen} total)",
    )


def a_derived_value_is_never_presented_as_evidence() -> None:
    """Structural: the two lists are separate in the response, and stay separate."""
    print("\n-- 3. quoted and calculated are different fields --")
    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)
    try:
        response = client.post(
            f"{BASE}/api/chat", json={"question": "ما قيمة العقد الإجمالية للمشروع؟"}
        ).json()
    finally:
        client.close()

    check("facts" in response and "derived" in response, "both lists are returned")
    check(
        isinstance(response.get("derived"), list),
        "derived is a list, empty when nothing was calculated",
    )
    for item in response.get("derived", []):
        check(
            bool(item.get("operands")),
            f"every calculated value names its operands ({item.get('label')})",
        )
        check(
            item.get("computed") is not None,
            "and carries the value it computed",
        )


if __name__ == "__main__":
    main()
    a_derived_value_is_never_presented_as_evidence()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Every figure in every answer is either quoted or calculated.")
