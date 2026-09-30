"""Application factory: wiring, error handling, routes and the static dashboard."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    auth, candidates, chat, chat_stream, documents, explain, health, integrations,
    knowledge, monitoring, users,
)
from app.config import get_settings
from app.container import Container
from app.exceptions import RagError
from app.logging_config import configure_logging
from app.models.database import init_database

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("Starting %s", settings.app_name)

    init_database()
    container = Container(settings)
    container.warmup()
    app.state.container = container

    logger.info(
        "Ready | llm=%s | embeddings=%s | collection=%s",
        settings.ollama_model,
        settings.embedding_model,
        settings.qdrant_collection,
    )
    try:
        yield
    finally:
        container.shutdown()
        logger.info("Shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="RAG Knowledge Base — Markdown MVP",
        version="1.0.0",
        lifespan=lifespan,
    )

    # The built client is served from this app, so it is same-origin. These entries are
    # for the Vite dev server, which runs on its own port during development.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(chat_stream.router)
    app.include_router(knowledge.router)
    app.include_router(explain.router)
    app.include_router(candidates.router)
    app.include_router(monitoring.router)
    # Registered before the single-page fallback below, which claims every path it is
    # given and would otherwise answer this one with the client's index.html.
    app.include_router(integrations.router)

    # The envelope gained three fields for the integration contract, all additive, so
    # nothing reading `error` or `detail` sees a change. A program must not branch on an
    # Arabic sentence: `error_code` is what it switches on, and `retryable` answers the
    # one question a machine client always has — status codes answer it only ambiguously,
    # since a 502 is worth another attempt and a 500 is not, though both are 5xx.
    @app.exception_handler(RagError)
    async def handle_rag_error(request: Request, exc: RagError) -> JSONResponse:
        logger.warning("%s: %s", type(exc).__name__, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": type(exc).__name__,
                "error_code": exc.code,
                "detail": exc.message,
                "request_id": getattr(request.state, "request_id", "") or "",
                "retryable": exc.retryable,
            },
            headers=exc.headers or None,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error")
        return JSONResponse(
            status_code=500,
            content={
                "error": "InternalServerError",
                "error_code": "INTERNAL_ERROR",
                "detail": f"خطأ غير متوقع: {exc}",
                "request_id": getattr(request.state, "request_id", "") or "",
                "retryable": False,
            },
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    web_dist = settings.web_dist_dir
    index_html = web_dist / "index.html"

    if index_html.exists():
        # The client is a single-page app: its hashed assets are served from /assets,
        # and every other unclaimed path returns index.html so a deep link such as
        # /documents/<id> is resolved by the router in the browser, not by 404 here.
        assets = web_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        async def spa_root() -> FileResponse:
            return FileResponse(index_html)

        @app.get("/{path:path}", include_in_schema=False)
        async def spa_fallback(path: str) -> FileResponse:
            candidate = web_dist / path
            if path and candidate.is_file() and candidate.resolve().is_relative_to(web_dist):
                return FileResponse(candidate)
            return FileResponse(index_html)

        logger.info("Serving web client from %s", web_dist)
    else:
        @app.get("/", include_in_schema=False)
        async def dashboard() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        logger.info("No web client build at %s — serving the legacy page", web_dist)

    return app


app = create_app()
