"""Qdrant vector store: collection lifecycle, upsert, search and per-document deletion."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient, models

from app.core.domain import Chunk
from app.exceptions import VectorStoreError

logger = logging.getLogger(__name__)

POINT_NAMESPACE = uuid.UUID("6f8b0f1e-4c3a-4f4d-9a71-4d1c2f9b6a21")


class QdrantVectorStore:
    def __init__(
        self,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.collection = collection
        self._url = url
        try:
            self._client = QdrantClient(url=url, api_key=api_key, timeout=int(timeout))
        except Exception as exc:  # noqa: BLE001 - surfaced as a domain error
            raise VectorStoreError(f"تعذر إنشاء الاتصال بـ Qdrant على {url}: {exc}") from exc

    def ensure_collection(self, dimension: int) -> None:
        try:
            if self._client.collection_exists(self.collection):
                self._assert_dimension(dimension)
                return

            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=dimension, distance=models.Distance.COSINE
                ),
            )
            for field in ("document_id", "category", "language"):
                self._client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            logger.info("Created Qdrant collection '%s' (dim=%s)", self.collection, dimension)
        except VectorStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"فشل تهيئة مجموعة Qdrant '{self.collection}': {exc}") from exc

    def _assert_dimension(self, dimension: int) -> None:
        info = self._client.get_collection(self.collection)
        params = info.config.params.vectors
        existing = params.size if isinstance(params, models.VectorParams) else None
        if existing is not None and existing != dimension:
            raise VectorStoreError(
                f"مجموعة Qdrant '{self.collection}' مهيأة بأبعاد {existing} بينما نموذج "
                f"الـEmbedding الحالي ينتج {dimension}. غيّر QDRANT_COLLECTION أو احذف المجموعة."
            )

    def upsert_chunks(self, chunks: list[Chunk], vectors: list[list[float]], payload_extra: dict[str, Any]) -> int:
        if len(chunks) != len(vectors):
            raise VectorStoreError("عدد الـChunks لا يطابق عدد المتجهات.")
        if not chunks:
            return 0

        points = [
            models.PointStruct(
                id=str(uuid.uuid5(POINT_NAMESPACE, chunk.chunk_id)),
                vector=vector,
                payload={
                    "document_id": chunk.document_id,
                    "chunk_id": chunk.chunk_id,
                    "filename": chunk.filename,
                    "chunk_index": chunk.index,
                    "heading": chunk.heading,
                    "section": chunk.section,
                    "page_or_section": chunk.section,
                    "heading_level": chunk.heading_level,
                    "content": chunk.content,
                    "char_count": chunk.char_count,
                    "section_id": chunk.section_id,
                    "parent_section_id": chunk.parent_section_id,
                    "parent_section": chunk.parent_section,
                    "document_title": chunk.document_title,
                    "has_table": chunk.has_table,
                    "has_list": chunk.has_list,
                    "has_code": chunk.has_code,
                    # Added alongside the existing fields rather than replacing any.
                    # Points written before this release simply lack them, and every
                    # reader treats a missing locator as the Markdown case, so the
                    # indexed corpus does not need rebuilding.
                    "locator": chunk.locator,
                    "page": chunk.page,
                    **payload_extra,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

        try:
            self._client.upsert(collection_name=self.collection, points=points, wait=True)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"فشل تخزين المتجهات في Qdrant: {exc}") from exc
        return len(points)

    def search(
        self,
        vector: list[float],
        top_k: int,
        score_threshold: float | None = None,
        document_ids: list[str] | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[models.Condition] = []
        if document_ids:
            conditions.append(
                models.FieldCondition(
                    key="document_id", match=models.MatchAny(any=document_ids)
                )
            )
        if category:
            conditions.append(
                models.FieldCondition(key="category", match=models.MatchValue(value=category))
            )

        try:
            response = self._client.query_points(
                collection_name=self.collection,
                query=vector,
                limit=top_k,
                with_payload=True,
                score_threshold=score_threshold,
                query_filter=models.Filter(must=conditions) if conditions else None,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"فشل البحث في Qdrant: {exc}") from exc

        return [{"score": point.score, **(point.payload or {})} for point in response.points]

    def delete_document(self, document_id: str) -> None:
        try:
            self._client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id", match=models.MatchValue(value=document_id)
                            )
                        ]
                    )
                ),
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"فشل حذف متجهات المستند من Qdrant: {exc}") from exc

    def list_document_chunks(self, document_id: str, limit: int = 500) -> list[dict[str, Any]]:
        try:
            points, _ = self._client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id", match=models.MatchValue(value=document_id)
                        )
                    ]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"فشل قراءة chunks المستند من Qdrant: {exc}") from exc

        payloads = [point.payload or {} for point in points]
        return sorted(payloads, key=lambda p: p.get("chunk_index", 0))

    def count_document(self, document_id: str) -> int:
        try:
            result = self._client.count(
                collection_name=self.collection,
                count_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id", match=models.MatchValue(value=document_id)
                        )
                    ]
                ),
                exact=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(f"فشل حساب متجهات المستند: {exc}") from exc
        return result.count

    def health(self) -> dict[str, Any]:
        try:
            exists = self._client.collection_exists(self.collection)
            points = self._client.count(self.collection, exact=True).count if exists else 0
            return {
                "reachable": True,
                "url": self._url,
                "collection": self.collection,
                "collection_exists": exists,
                "points": points,
            }
        except Exception as exc:  # noqa: BLE001
            return {"reachable": False, "url": self._url, "error": str(exc)}
