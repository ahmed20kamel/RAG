"""What similarity the knowledge-only gate actually sees.

The experiment showed a probe question refusing in `eligible` mode even though a taught
item answered it word for word. Either the threshold is set above where real matches
land, or the scorer never reaches the item. The difference matters — one is a tuning
number, the other is a defect — and neither is decidable from the outcome alone.

So the score is read rather than inferred. The item is taught over the API exactly as a
person would teach it, then the same vector search the gate uses is called directly and
its number printed next to the threshold it would be compared against.

Run: python tests/knowledge_only_threshold_probe.py   (needs the app running)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Building the container reconfigures logging, which closes a stdout wrapper made
# here; reconfiguring the existing stream instead survives that.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from tests import harness_auth  # noqa: E402
from tests.knowledge_only_experiment import TAUGHT  # noqa: E402

BASE = os.environ.get("RAG_BASE_URL", "http://localhost:8000").rstrip("/")


def main() -> None:
    from app.config import get_settings
    from app.container import Container

    settings = get_settings()
    container = Container(settings)
    index = container.knowledge_index
    embedder = container.embedder
    threshold = settings.knowledge_only_threshold
    supplementary = settings.knowledge_score_threshold

    print(f"knowledge_only_threshold  = {threshold}   (answering alone)")
    print(f"knowledge_score_threshold = {supplementary} (supplementing documents)\n")

    client = httpx.Client(timeout=900)
    harness_auth.login_admin(client, BASE)
    user_id = client.get(f"{BASE}/api/auth/me").json()["id"]

    created: list[str] = []
    try:
        for entry in TAUGHT:
            made = client.post(
                f"{BASE}/api/knowledge",
                json={
                    "type": "fact",
                    "scope": "global",
                    "content": entry["content"],
                    "source_text": entry["source_text"],
                },
            )
            made.raise_for_status()
            item_id = made.json()["id"]
            created.append(item_id)
            client.post(f"{BASE}/api/knowledge/{item_id}/approve", json={"reason": "قياس"})
            client.post(f"{BASE}/api/knowledge/{item_id}/activate", json={"reason": ""})
        time.sleep(5)

        print(f"{'question':56} {'best':>7} {'gate':>18}")
        for entry, item_id in zip(TAUGHT, created):
            vector = embedder.embed_one(entry["question"])
            hits = index.search(vector, user_id=user_id, team_id=None, score_threshold=0.0)
            scores = {h.item_id: h.score for h in hits}
            best = scores.get(item_id)
            if best is None:
                verdict = "NOT RETRIEVED"
            elif best >= threshold:
                verdict = "answers alone"
            elif best >= supplementary:
                verdict = "supplements only"
            else:
                verdict = "not used at all"
            shown = f"{best:.3f}" if best is not None else "  —  "
            print(f"  {entry['question'][:54]:56} {shown:>7} {verdict:>18}")

        print(f"\n  items returned by the search at all: {len(hits)}")
    finally:
        for item_id in created:
            client.post(f"{BASE}/api/knowledge/{item_id}/archive", json={"reason": "تنظيف"})
        client.close()


if __name__ == "__main__":
    main()
