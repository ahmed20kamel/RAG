"""Ten traps: a value the file states twice, once wrongly and once corrected.

Every question here is asked plainly, the way somebody who does not know there is a
conflict would ask it. Nothing hints that two versions exist. That is the whole design —
a question that says "what is the *corrected* figure?" tests nothing, because it has
already supplied the answer.

What this separates is the question no other suite can answer: did retrieval get better,
or did a larger model simply become more cautious? A cautious model hedges; it does not
reliably name the corrected value when the superseded one is sitting beside it in the
same context, ranked higher, and matching the question's wording more closely.

Each trap carries three things:

    wanted      the value in force, which a correct answer states
    superseded  the value it replaced, which a correct answer must not present as current
    anchor      the words the corpus actually uses, so a passing answer cannot be a
                lucky paraphrase

Scoring is deliberately strict on one point. An answer that states the corrected value
*and* mentions the old one as history passes; an answer that leads with the old value
fails, even if the correction appears later. A reader skimming the first two lines is
the person this system exists to protect.

Run:  python tests/test_superseded_traps.py   (needs the app running)
"""

from __future__ import annotations

import io
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from app.core.text import normalize  # noqa: E402
from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
#: How much of the answer counts as "leading with". Two or three sentences — what
#: somebody reads before deciding they have their answer.
LEAD_CHARS = 420


@dataclass
class Trap:
    """One question with a right answer and a plausible wrong one already in the file."""

    qid: str
    question: str
    #: Any one of these, stated anywhere, means the corrected value was found.
    wanted: tuple[str, ...]
    #: Any of these appearing *first* means the superseded value was served as current.
    superseded: tuple[str, ...]
    note: str = ""
    #: Set when the corpus turned out not to support the trap, so a miss is not scored.
    skip: bool = False
    result: str = field(default="", init=False)


TRAPS: list[Trap] = [
    Trap(
        "T1",
        "ما بنود المأمورية المكلَّف بها الخبير الهندسي؟",
        wanted=("3 بنود", "ثلاثة بنود", "ثلاث بنود"),
        superseded=("5 بنود", "خمسة بنود", "7 بنود", "سبعة بنود", "4 بنود"),
        note="المأمورية صُحِّحت من 5/7 إلى 3 بنود بالحكم المصحح",
    ),
    Trap(
        "T2",
        "كم بندًا تتضمنه مأمورية الخبرة النافذة؟",
        wanted=("3", "ثلاثة", "ثلاث"),
        superseded=("5 بنود", "7 بنود", "خمسة بنود"),
        note="صياغة ثانية لنفس الفخ",
    ),
    Trap(
        "T3",
        "ما البنود التي حُذفت من مأمورية الخبير؟",
        wanted=("4", "5", "المواد المشونة", "الكفالات"),
        superseded=(),
        note="يجب أن يعرف أن هناك بنودًا محذوفة أصلًا",
    ),
    Trap(
        "T4",
        "ما قيمة الكشف المالي المعتمد للأعمال؟",
        wanted=("150,420", "150420"),
        superseded=("121,095", "121095"),
        note="الكشف الأصلي 121,095 ثم المحدَّث 150,420 بتاريخ 10/07",
    ),
    Trap(
        "T5",
        "ما السقف الأقصى لغرامة التأخير؟",
        wanted=("268,000", "268000"),
        superseded=(),
        note="رقم منصوص عليه صراحةً — اختبار التزام بالمصدر",
    ),
    Trap(
        "T6",
        "ما قيمة غرامة التأخير اليومية؟",
        wanted=("1,985.19", "1985.19"),
        superseded=(),
        note="رقم منصوص عليه صراحةً",
    ),
    Trap(
        "T7",
        "ما صفة المدعى عليها في هذه القضية؟",
        wanted=("القطعة المجاورة", "مقاول مشروع القطعة", "17"),
        superseded=("مقاول من الباطن", "من الباطن"),
        note="التكييف صُحِّح من «مقاول من الباطن» إلى «مقاول مشروع القطعة المجاورة»",
    ),
    Trap(
        "T8",
        "أي قطعة أرض هي موقع مشروع المدعية؟",
        wanted=("18",),
        superseded=(),
        note="صُحِّح موقع المشروع من 17 إلى 18",
    ),
    Trap(
        "T9",
        "هل الخبير ملزم بالانتقال إلى بلدية أبوظبي؟",
        wanted=("جواز", "تخويل", "ليس", "لا يلزم", "غير ملزم", "من طلبات", "الصحيفة"),
        superseded=("ملزم بالانتقال", "يجب على الخبير الانتقال"),
        note="البند 7 من طلبات الصحيفة لا من المنطوق — تخويل جوازي",
    ),
    Trap(
        "T10",
        "من الذي يحدد مدة إعادة التنفيذ؟",
        wanted=("المقاول", "اليافور"),
        superseded=("الاستشاري",),
        note="الملف ينص أن المقاول هو من يحدد المدة وليس الاستشاري",
    ),
]


def check(trap: Trap, answer: str) -> None:
    lead = normalize(answer[:LEAD_CHARS])
    whole = normalize(answer)

    found = any(normalize(w) in whole for w in trap.wanted)
    served_old = any(normalize(s) in lead for s in trap.superseded) if trap.superseded else False

    if found and not served_old:
        trap.result = "PASS"
    elif found and served_old:
        trap.result = "MIXED"
    else:
        trap.result = "FAIL"

    icon = {"PASS": "✅", "MIXED": "⚠️", "FAIL": "❌"}[trap.result]
    print(f"[{icon}] {trap.qid}  {trap.question}")
    print(f"        {trap.note}")
    if trap.result != "PASS":
        print(f"        الإجابة: {answer[:190].replace(chr(10), ' ')}")


def main() -> None:
    print(f"أسئلة الفخّ — نسخة قديمة ونسخة مصححة، بلا أي تلميح  ({BASE})")
    with httpx.Client(timeout=1200) as client:
        harness_auth.login(client, BASE)
        model = client.get(f"{BASE}/api/config").json().get("ollama_model", "?")
        print(f"النموذج: {model}\n")

        for trap in TRAPS:
            started = time.time()
            try:
                reply = client.post(f"{BASE}/api/chat", json={"question": trap.question})
                reply.raise_for_status()
                answer = reply.json().get("answer", "")
            except httpx.HTTPError as exc:
                trap.result = "ERROR"
                print(f"[💥] {trap.qid}  تعذّر: {type(exc).__name__}")
                continue
            check(trap, answer)
            print(f"        [{time.time() - started:.0f}ث]\n")

    counts = {r: sum(1 for t in TRAPS if t.result == r) for r in ("PASS", "MIXED", "FAIL", "ERROR")}
    scored = len(TRAPS) - counts["ERROR"]
    print("=" * 70)
    print(f"صحيح تمامًا: {counts['PASS']}   مختلط: {counts['MIXED']}   خطأ: {counts['FAIL']}"
          + (f"   تعذّر: {counts['ERROR']}" if counts["ERROR"] else ""))
    if scored:
        print(f"النسبة: {counts['PASS'] / scored * 100:.0f}% صحيح تمامًا، "
              f"{(counts['PASS'] + counts['MIXED']) / scored * 100:.0f}% وجد القيمة الصحيحة")
    print("\n(مختلط = ذكر القيمة الصحيحة لكنه بدأ بالقديمة — صحيح لمن يقرأ كاملًا،")
    print(" ومضلِّل لمن يقرأ أول سطرين.)")


if __name__ == "__main__":
    main()
