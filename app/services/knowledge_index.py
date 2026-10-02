"""Semantic index for approved knowledge, in its own Qdrant collection.

The separation is the design, not a convenience. Taught claims and document passages are
never scored against each other in one vector space: a colleague's sentence would compete
with a contract clause for the same slot, and the ranking would decide something the
approval workflow is supposed to decide. Two collections, two searches, and the priority
rule applied afterwards over metadata.

The index mirrors exactly one thing: the set of ACTIVE items. Activation adds a point,
and archiving, rejecting or editing removes it — so a vector can never outlive the
approval that justified it. Every write is best-effort: the knowledge layer is an
addition to the system, and an embedding host that is down must not stop an answer or
block a reviewer.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.core.knowledge import KnowledgeScope
from app.exceptions import VectorStoreError

logger = logging.getLogger(__name__)

#: Qdrant needs a UUID or an integer point id; knowledge item ids are already UUIDs, so
#: they are used directly and an item owns exactly one point.
SEARCH_LIMIT = 24


@dataclass(slots=True)
class KnowledgeHit:
    item_id: str
    version_id: str
    score: float
    payload: dict[str, Any]


class KnowledgeVectorIndex:
    def __init__(
        self,
        *,
        url: str,
        collection: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.collection = collection
        self._client = QdrantClient(url=url, api_key=api_key, timeout=timeout)
        self._ready = False
        #: Last search failure, surfaced in stats so a degraded index is visible.
        self.last_error = ""

    # -- lifecycle --------------------------------------------------------
    def ensure_collection(self, dimension: int) -> None:
        try:
            if self._client.collection_exists(self.collection):
                self._ready = True
                return
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=dimension, distance=qmodels.Distance.COSINE
                ),
            )
            # The retrieval query always filters on these before ranking, so they are
            # indexed rather than scanned.
            for field, schema in (
                ("scope", qmodels.PayloadSchemaType.KEYWORD),
                ("type", qmodels.PayloadSchemaType.KEYWORD),
                ("owner_user_id", qmodels.PayloadSchemaType.KEYWORD),
                ("owner_team_id", qmodels.PayloadSchemaType.KEYWORD),
            ):
                self._client.create_payload_index(
                    collection_name=self.collection, field_name=field, field_schema=schema
                )
            self._ready = True
            logger.info("Created knowledge collection '%s' (dim=%s)", self.collection, dimension)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreError(
                f"فشل تهيئة مجموعة المعرفة '{self.collection}': {exc}"
            ) from exc

    # -- writing ----------------------------------------------------------
    def upsert(self, *, item_id: str, version_id: str, vector: list[float], payload: dict) -> bool:
        """Adds or replaces the point for one item. False when the store refused."""
        try:
            self._client.upsert(
                collection_name=self.collection,
                points=[
                    qmodels.PointStruct(
                        id=self._point_id(item_id),
                        vector=vector,
                        payload={**payload, "item_id": item_id, "version_id": version_id},
                    )
                ],
                wait=True,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not index knowledge item %s: %s", item_id, exc)
            return False

    def remove(self, item_id: str) -> bool:
        """Drops an item's point. Called whenever it stops being ACTIVE."""
        try:
            self._client.delete(
                collection_name=self.collection,
                points_selector=qmodels.PointIdsList(points=[self._point_id(item_id)]),
                wait=True,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not remove knowledge item %s from the index: %s", item_id, exc)
            return False

    # -- reading ----------------------------------------------------------
    def search(
        self,
        vector: list[float],
        *,
        user_id: str,
        team_id: str | None,
        limit: int = SEARCH_LIMIT,
        score_threshold: float = 0.0,
    ) -> list[KnowledgeHit]:
        """Semantic search, with scope isolation applied *in the query*.

        The filter is part of the request rather than a pass over the results, so a
        personal item belonging to someone else is never fetched in the first place —
        there is no moment at which it exists in memory to be leaked by a later bug.
        """
        reach: list[qmodels.Condition] = [
            qmodels.FieldCondition(
                key="scope",
                match=qmodels.MatchAny(
                    any=[KnowledgeScope.GLOBAL.value, KnowledgeScope.DEPARTMENT.value]
                ),
            ),
            qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="scope", match=qmodels.MatchValue(value=KnowledgeScope.USER.value)
                    ),
                    qmodels.FieldCondition(
                        key="owner_user_id", match=qmodels.MatchValue(value=user_id)
                    ),
                ]
            ),
        ]
        if team_id:
            reach.append(
                qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="scope", match=qmodels.MatchValue(value=KnowledgeScope.TEAM.value)
                        ),
                        qmodels.FieldCondition(
                            key="owner_team_id", match=qmodels.MatchValue(value=team_id)
                        ),
                    ]
                )
            )

        try:
            results = self._client.query_points(
                collection_name=self.collection,
                query=vector,
                limit=limit,
                score_threshold=score_threshold or None,
                # `should` alone already requires at least one match in this client
                # version; passing min_should as a bare int is rejected by its model.
                query_filter=qmodels.Filter(should=reach),
                with_payload=True,
            ).points
        except Exception as exc:  # noqa: BLE001
            # A missing or unreachable collection means no semantic hits, not a failed
            # answer: the caller falls back to lexical selection. Logged at error level
            # because a silently degraded search looks exactly like an empty knowledge
            # base — that confusion already cost one debugging session here.
            self.last_error = str(exc)
            logger.error("Knowledge search failed, falling back to lexical: %s", exc)
            return []

        self.last_error = ""

        return [
            KnowledgeHit(
                item_id=str(point.payload.get("item_id", "")),
                version_id=str(point.payload.get("version_id", "")),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in results
        ]

    def stats(self) -> dict[str, Any]:
        try:
            exists = self._client.collection_exists(self.collection)
            points = self._client.count(self.collection, exact=True).count if exists else 0
            return {
                "collection": self.collection,
                "exists": exists,
                "points": points,
                "last_error": self.last_error,
            }
        except Exception as exc:  # noqa: BLE001
            return {"collection": self.collection, "exists": False, "points": 0, "error": str(exc)}

    @staticmethod
    def _point_id(item_id: str) -> str:
        try:
            return str(uuid.UUID(item_id))
        except ValueError:
            # Anything that is not already a UUID gets a stable derived one.
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowledge:{item_id}"))
