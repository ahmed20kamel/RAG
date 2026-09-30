"""Approved knowledge for regression run C, seeded and removed again.

Runs A and B answered a narrower question: does the layer cost anything when it has
nothing in it? An empty knowledge base cannot interfere with a retrieval it never joins,
so B proves the plumbing is inert, not that the layer is safe.

C is the real test. It puts approved, active, company-wide knowledge in front of the same
thirty-eight questions — and deliberately picks subjects the documents already cover, so
the retrieval arm genuinely fires on them. A retention definition sits next to the
question about retention; a currency rule applies to every numeric answer in the set. If
taught material can pull a document-grounded answer off course, these are the items that
would do it.

Nothing here contradicts a gold answer. A contradiction would be a conflict test, which
`test_authority_and_conflicts.py` already covers; what is measured here is interference
in the ordinary case, where the knowledge is true, relevant and simply present.

    python tests/seed_regression_c.py --seed    # before the eval
    python tests/seed_regression_c.py --clean   # after it
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

from tests import harness_auth  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")
STATE = ROOT / "tests" / "eval" / "results" / "regression_c_seeded.json"

#: Six items, every type the layer supports, all scoped globally so they carry the most
#: authority a taught claim can carry. Chosen to overlap the corpus, not to fight it.
ITEMS = [
    {
        "type": "terminology",
        "scope": "global",
        "content": (
            "المحتجزات هي نسبة تُستقطع من كل دفعة مستحقة للمقاول وتُفرج عنها "
            "عند انتهاء فترة الضمان التعاقدية."
        ),
        "source_text": "الدليل الداخلي للإدارة المالية",
    },
    {
        "type": "terminology",
        "scope": "global",
        "content": (
            "المعاينة الهندسية هي الكشف الميداني الذي تنتدب له المحكمة خبيرًا "
            "لتقدير الأضرار وتحديد أسبابها في نزاعات المقاولات."
        ),
        "source_text": "مذكرة الإدارة القانونية",
    },
    {
        "type": "rule",
        "scope": "global",
        "content": "عند ذكر أي مبلغ مالي في الإجابة، اكتب اسم العملة صراحةً بعد الرقم.",
        "source_text": "قرار توحيد صياغة التقارير",
    },
    {
        "type": "rule",
        "scope": "global",
        "content": (
            "عند اختلاف مصدرين حول قيمة واحدة، اذكر القيمتين ومصدر كل منهما "
            "ولا تختر إحداهما."
        ),
        "source_text": "قرار توحيد صياغة التقارير",
    },
    {
        "type": "procedure",
        "scope": "global",
        "content": (
            "إجراء تسجيل مطالبة داخلية: يُفتح ملف المطالبة، ثم يُرفق الإخطار الخطي "
            "ومرفقاته، ثم تُحال إلى الإدارة القانونية خلال خمسة أيام عمل."
        ),
        "source_text": "دليل إجراءات المطالبات",
    },
    {
        "type": "fact",
        "scope": "global",
        "content": (
            "الإدارة القانونية هي الجهة المختصة بمتابعة دعاوى الشركة أمام محاكم أبوظبي."
        ),
        "source_text": "الهيكل التنظيمي المعتمد",
    },
]


def seed() -> None:
    client = httpx.Client(timeout=300)
    harness_auth.login_admin(client, BASE)
    created: list[str] = []
    try:
        for item in ITEMS:
            made = client.post(f"{BASE}/api/knowledge", json=item)
            made.raise_for_status()
            item_id = made.json()["id"]
            created.append(item_id)
            client.post(
                f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "تهيئة تشغيل C"}
            ).raise_for_status()
            client.post(
                f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""}
            ).raise_for_status()
            print(f"  seeded {item['type']:12} {item_id}")

        time.sleep(4)  # the vectors are written asynchronously
        active = client.get(f"{BASE}/api/knowledge", params={"status": "active"}).json()
        print(f"\nactive knowledge items now: {active['total']}")
        stats = client.get(f"{BASE}/api/health").json().get("knowledge_index", {})
        print(f"knowledge index: {stats}")
    finally:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(created, indent=2), encoding="utf-8")
        client.close()


def clean() -> None:
    if not STATE.exists():
        print("nothing recorded as seeded")
        return
    client = httpx.Client(timeout=300)
    harness_auth.login_admin(client, BASE)
    try:
        for item_id in json.loads(STATE.read_text(encoding="utf-8")):
            response = client.post(
                f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "انتهاء تشغيل C"}
            )
            print(f"  archived {item_id} ({response.status_code})")
    finally:
        client.close()
        STATE.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if args.clean:
        clean()
    elif args.seed:
        seed()
    else:
        parser.error("pass --seed or --clean")
