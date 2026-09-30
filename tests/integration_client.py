"""A minimal signing client for the ERP integration endpoint.

Written to be readable rather than clever, because it is also the reference the calling
system implements against. Three things it demonstrates that prose about the contract
cannot:

* the secret never leaves this process — only the key id travels;
* the signature covers the exact bytes sent, so the body is serialised once and those
  same bytes are both signed and posted;
* the nonce is fresh per request, which is what makes a captured request useless twice.

Run directly for a smoke call:

    python tests/integration_client.py "ما سياسة المشتريات؟"
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENDPOINT_PATH = "/api/v1/integrations/erp/query"


def sign(secret: str, method: str, path: str, body: bytes, timestamp: str, nonce: str) -> str:
    """HMAC-SHA256 over the canonical material, in the agreed order."""
    material = "\n".join(
        [method.upper(), path, hashlib.sha256(body).hexdigest(), timestamp, nonce]
    )
    return hmac.new(
        secret.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class ErpRagClient:
    """What the ERP side would hold: a base URL, a key id, and a secret."""

    def __init__(
        self,
        base_url: str,
        key: str,
        secret: str,
        *,
        tenant_id: str,
        timeout: float = 120.0,
        connect_timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.secret = secret
        self.tenant_id = tenant_id
        self.timeout = httpx.Timeout(timeout, connect=connect_timeout)

    def build(
        self,
        query: str,
        *,
        request_id: str = "",
        external_user_id: str = "erp-user-test",
        tenant_id: str | None = None,
        erp_facts: list[dict] | None = None,
        web_search_allowed: bool = False,
        retrieval: dict | None = None,
        language: str | None = None,
    ) -> dict:
        payload: dict = {
            "request_id": request_id or f"erp-{uuid.uuid4()}",
            "tenant_id": self.tenant_id if tenant_id is None else tenant_id,
            "query": query,
            "end_user": {"external_user_id": external_user_id},
            "web_search_allowed": web_search_allowed,
        }
        if retrieval:
            payload["retrieval"] = retrieval
        if language:
            payload["language"] = language
        if erp_facts is not None:
            payload["context"] = {"erp_facts": erp_facts}
        return payload

    def post(
        self,
        payload: dict,
        *,
        idempotency_key: str = "",
        timestamp: int | None = None,
        nonce: str = "",
        tamper_body: bytes | None = None,
        signature: str | None = None,
        authorization: str | None = None,
        client: httpx.Client | None = None,
    ) -> httpx.Response:
        """Signs and sends. The override arguments exist so the tests can forge."""
        # Serialised once. Signing a re-serialisation would verify a body nobody sent.
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stamp = str(int(time.time()) if timestamp is None else timestamp)
        once = nonce or secrets.token_hex(16)

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": authorization if authorization is not None else f"Bearer {self.key}",
            "X-RAG-Key-Id": self.key.removeprefix("rag_sk_"),
            "X-RAG-Timestamp": stamp,
            "X-RAG-Nonce": once,
            "X-RAG-Signature": (
                signature
                if signature is not None
                else sign(self.secret, "POST", ENDPOINT_PATH, body, stamp, once)
            ),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        sent = tamper_body if tamper_body is not None else body
        owned = client is None
        http = client or httpx.Client(timeout=self.timeout)
        try:
            return http.post(self.base_url + ENDPOINT_PATH, content=sent, headers=headers)
        finally:
            if owned:
                http.close()

    def ask(self, query: str, **kwargs) -> httpx.Response:
        return self.post(self.build(query, **kwargs))


def from_env() -> ErpRagClient:
    """Builds a client from the environment the test harness sets."""
    missing = [
        name
        for name in ("ERP_RAG_KEY", "ERP_RAG_SECRET", "ERP_RAG_TENANT")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"missing environment: {', '.join(missing)}")
    return ErpRagClient(
        os.environ.get("RAG_BASE_URL", "http://localhost:8000"),
        os.environ["ERP_RAG_KEY"],
        os.environ["ERP_RAG_SECRET"],
        tenant_id=os.environ["ERP_RAG_TENANT"],
    )


if __name__ == "__main__":
    question = sys.argv[1] if len(sys.argv) > 1 else "ما سياسة المشتريات؟"
    reply = from_env().ask(question)
    print(reply.status_code)
    print(json.dumps(reply.json(), ensure_ascii=False, indent=2)[:4000])
