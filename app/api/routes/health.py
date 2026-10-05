"""Health and effective-configuration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.exceptions import AuthorizationError

from app.api.deps import ContainerDep, CurrentUserDep, SettingsDep

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system/busy")
def busy(request: Request) -> dict[str, object]:
    """Whether anyone is waiting on an answer — asked by the update task before it
    restarts the server, so an update never cuts a question off. Answered only to this
    machine itself: it says nothing secret, but nobody else needs it."""
    from app.services.activity import ACTIVITY

    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        raise AuthorizationError("متاح من الخادم نفسه فقط.")
    return {"busy": ACTIVITY.active > 0, "active": ACTIVITY.active}


@router.get("/health")
def health(container: ContainerDep, _user: CurrentUserDep) -> dict[str, object]:
    ollama = container.llm.health()
    embeddings = container.embedder.health()
    qdrant = container.vector_store.health()
    ready = bool(ollama.get("reachable") and embeddings.get("reachable") and qdrant.get("reachable"))
    return {
        "status": "ok" if ready else "degraded",
        "ollama": ollama,
        "embeddings": embeddings,
        "qdrant": qdrant,
        "keyword_index": container.keyword_index.stats(),
        "knowledge_index": container.knowledge.stats(),
        "embedding_cache": {
            "hits": container.retriever.cache.hits,
            "misses": container.retriever.cache.misses,
        },
        "reranker": {"name": container.reranker.name, **(
            container.reranker.scorer.status.as_dict()
            if getattr(container.reranker, "scorer", None) is not None else {}
        )},
    }


@router.get("/config")
def effective_config(
    settings: SettingsDep, container: ContainerDep, _user: CurrentUserDep
) -> dict[str, object]:
    """Non-secret configuration, so the UI can show what the backend is actually using."""
    return {
        "ollama_base_url": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        "ollama_num_ctx": settings.ollama_num_ctx,
        "ollama_think": settings.ollama_think,
        "embedding_base_url": settings.resolved_embedding_base_url,
        "embedding_model": settings.embedding_model,
        "qdrant_collection": settings.qdrant_collection,
        "top_k": settings.top_k,
        "wide_top_k": settings.wide_top_k,
        "candidate_pool": settings.candidate_pool,
        "score_threshold": settings.score_threshold,
        "vector_score_floor": settings.vector_score_floor,
        "min_rerank_score": settings.min_rerank_score,
        "max_context_chars": settings.max_context_chars,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "reranker": container.reranker.name,
        "hybrid": {
            "keyword_search": settings.enable_keyword_search,
            "entity_retrieval": settings.enable_entity_retrieval,
            "expansion": settings.enable_expansion,
        },
        "supported_extensions": container.parser_registry.effective_extensions(
            container.settings.allowed_extensions
        ),
    }
