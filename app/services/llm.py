"""Chat client backed by Ollama."""

from __future__ import annotations

import logging
import re

import httpx

from app.exceptions import LLMError

logger = logging.getLogger(__name__)

THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class OllamaLLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.1,
        num_ctx: int = 8192,
        timeout: float = 300.0,
        think: bool = False,
        keep_alive: str = "30m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.think = think
        self.keep_alive = keep_alive

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        body: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            # Without this the model is evicted between questions and every question
            # pays the full load cost again on a machine this size.
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if not self.think:
            body["think"] = False

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/api/chat", json=body)
                if response.status_code == 400 and "think" in body:
                    body.pop("think")
                    response = client.post(f"{self.base_url}/api/chat", json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"فشل استدعاء النموذج '{self.model}': {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"تعذر الاتصال بخدمة Ollama على {self.base_url}: {exc}") from exc

        content = (payload.get("message") or {}).get("content", "")
        if not content:
            raise LLMError(f"النموذج '{self.model}' أعاد إجابة فارغة.")
        return THINK_BLOCK.sub("", content).strip()

    def health(self) -> dict[str, object]:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                names = {m.get("name", "") for m in response.json().get("models", [])}
            available = self.model in names or f"{self.model}:latest" in names
            return {
                "reachable": True,
                "url": self.base_url,
                "model": self.model,
                "model_available": available,
            }
        except httpx.HTTPError as exc:
            return {
                "reachable": False,
                "url": self.base_url,
                "model": self.model,
                "error": str(exc),
            }
