"""Embedding client backed by Ollama. The model name always comes from configuration."""

from __future__ import annotations

import logging

import httpx

from app.exceptions import EmbeddingError

logger = logging.getLogger(__name__)


class OllamaEmbeddingClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        batch_size: int = 16,
        timeout: float = 300.0,
        keep_alive: str = "30m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = max(1, batch_size)
        self.timeout = timeout
        self.keep_alive = keep_alive
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self.embed_one("dimension probe"))
        return self._dimension

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        with httpx.Client(timeout=self.timeout) as client:
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start : start + self.batch_size]
                vectors.extend(self._embed_batch(client, batch))

        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"نموذج الـEmbedding أعاد {len(vectors)} متجهًا مقابل {len(texts)} نصًا."
            )
        self._dimension = len(vectors[0])
        return vectors

    def _embed_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        try:
            response = client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": batch, "keep_alive": self.keep_alive},
            )
            if response.status_code == 404:
                return [self._embed_legacy(client, text) for text in batch]
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise EmbeddingError(
                f"فشل إنشاء الـEmbeddings عبر النموذج '{self.model}': {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"تعذر الاتصال بخدمة Ollama على {self.base_url}: {exc}"
            ) from exc

        embeddings = payload.get("embeddings")
        if not embeddings:
            raise EmbeddingError(f"استجابة غير متوقعة من نموذج الـEmbedding '{self.model}'.")
        return embeddings

    def _embed_legacy(self, client: httpx.Client, text: str) -> list[float]:
        response = client.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model, "prompt": text},
        )
        response.raise_for_status()
        embedding = response.json().get("embedding")
        if not embedding:
            raise EmbeddingError(f"استجابة غير متوقعة من نموذج الـEmbedding '{self.model}'.")
        return embedding

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
