"""Application configuration. Every value is overridable via environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "RAG Knowledge Base"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:latest"
    ollama_temperature: float = 0.0
    # Sized so the 14B model stays fully resident in 12 GB of VRAM. Raising this
    # inflates the KV cache and spills part of the model to the CPU, which showed
    # up as 400-second answers and 600-second timeouts.
    ollama_num_ctx: int = 16384
    ollama_timeout: float = 600.0
    ollama_think: bool = False
    ollama_keep_alive: str = "30m"

    embedding_model: str = "bge-m3:latest"
    embedding_batch_size: int = 16
    embedding_timeout: float = 300.0
    embedding_keep_alive: str = "30m"
    # Generation and embeddings can live on different Ollama hosts: the LLM belongs
    # wherever the GPU is, while embeddings must stay on the host whose model produced
    # the vectors already in Qdrant. Empty means "same host as generation".
    embedding_base_url: str = ""

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "alyafour_knowledge_base"
    qdrant_timeout: float = 60.0

    top_k: int = 8
    wide_top_k: int = 14
    candidate_pool: int = 30
    score_threshold: float = 0.35
    vector_score_floor: float = 0.25
    min_rerank_score: float = 0.15
    # Reranking. "feature" scores countable signals only; "cross" adds a local
    # cross-encoder on top of them and falls back to "feature" if the model is absent.
    reranker: str = "feature"
    reranker_model_dir: str = "models/reranker"
    #: Added as weight × score, score in [0, 1]. Sized against the feature terms so the
    #: model decides among plausible passages without overriding a named date.
    reranker_weight: float = 2.0
    #: How many of the best feature-ranked candidates the model reads.
    reranker_top_n: int = 12
    reranker_max_tokens: int = 256
    #: 0 = half the logical cores, which measured fastest on a 6-core laptop.
    reranker_threads: int = 0
    #: Per question. Past it, the feature ranking is kept for that question.
    reranker_budget_ms: int = 15000

    # Backups. Relative paths resolve against the project root.
    backup_dir: str = "backups"
    #: A second copy on another disk or an internal share. Strongly recommended: a
    #: backup on the same disk as the data does not survive the disk.
    backup_mirror_dir: str | None = None
    backup_keep_daily: int = 7
    backup_keep_weekly: int = 4
    #: The archive then carries INTEGRATION_MASTER_KEY, without which every issued ERP
    #: credential is lost. Store the archives as secrets.
    backup_include_env: bool = True
    #: The health report flags backups older than this.
    backup_max_age_hours: int = 26

    #: Mask phone numbers, e-mail addresses, passcodes, meeting ids, Emirates ids and
    #: IBANs in answers unless the question asks for that kind of detail.
    redact_sensitive: bool = True

    # Monitoring. Alerts on the dashboard fire past these, once there are enough requests.
    monitor_p95_ms: int = 240_000
    monitor_error_rate: float = 0.05
    monitor_refusal_rate: float = 0.40
    #: A bearer token that lets a monitoring system scrape /api/metrics without a user
    #: session. Unset, the endpoint needs a signed-in administrator.
    metrics_token: str | None = None
    # How many of the question's own terms the leading candidates must cover before a
    # question with no strong semantic match counts as covered at all.
    min_covered_terms: int = 1
    # Slots kept for the best pure-similarity hits so lexical features cannot bury them.
    reserved_semantic_slots: int = 3
    # Dated sections swept for a timeline question, newest first.
    timeline_sections: int = 14
    max_expanded_candidates: int = 60
    primary_evidence_count: int = 3
    # These budgets were tuned for CPU generation. With the LLM on a remote GPU the
    # limiting factor is the model's context window, not prefill speed, so they are
    # sized for recall instead — still well inside OLLAMA_NUM_CTX.
    max_context_chars: int = 20000
    narrow_context_chars: int = 11000
    max_related_blocks: int = 4
    # Verbatim label/value facts placed above the excerpts. Each costs ~60 chars.
    max_facts_in_prompt: int = 45
    # A completion pass re-answers from the same evidence when validation proves the
    # answer omitted a supported fact. It cannot introduce anything new.
    completeness_retry: bool = True
    # Targeted completion runs at most this many times; it never loops.
    max_completion_passes: int = 1
    enable_keyword_search: bool = True
    enable_entity_retrieval: bool = True
    enable_expansion: bool = True

    chunk_size: int = 1400
    chunk_overlap: int = 200
    min_chunk_size: int = 200
    max_heading_depth: int = 4

    upload_dir: Path = BASE_DIR / "data" / "uploads"
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'rag.db').as_posix()}"
    max_upload_bytes: int = 20 * 1024 * 1024

    # Web fallback. Off by default: the system's value is that its answers come from
    # documents the company owns, and turning this on is a decision to let some of
    # them come from elsewhere. It is consulted only after the corpus has refused.
    web_search_enabled: bool = False
    web_search_max_results: int = 5
    web_search_timeout: float = 12.0
    #: Comma-separated. Empty means anywhere; a populated list restricts the system
    #: to sources an operator has decided to stand behind.
    web_search_allowed_domains: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    #: Scanned pages are read only when this is on and the engine is actually
    #: installed. Off, a scan is refused as `ocr_required` rather than indexed empty.
    enable_ocr: bool = True
    #: Tables of scanned PDFs read by a RAGFlow instance and checked against our own
    #: reading (app/services/table_assist.py). Off unless a RAGFlow URL and key are set.
    table_assist_enabled: bool = False
    ragflow_url: str = ""
    ragflow_api_key: str = ""
    table_assist_dataset: str = "table_assist"
    table_assist_timeout: float = 1800.0
    #: Each indexed document is sorted into a project and type folder by reading its
    #: first page (app/services/organizer.py). What an upload already said is kept.
    auto_organize_enabled: bool = True
    #: Repeated questions answered from memory while the reader's documents and the
    #: knowledge base are unchanged (app/services/learning_loop.py).
    answer_memory_enabled: bool = True
    #: Speech to text for the chat's microphone, run on this machine (faster-whisper):
    #: the browser's own dictation sends the audio to an outside service. "small" reads
    #: Arabic acceptably on a CPU in a few seconds; "medium" reads it better, slower.
    speech_enabled: bool = True
    speech_model: str = "small"
    #: "ar" for Arabic; empty to let the model detect the language of each recording.
    speech_language: str = "ar"
    speech_max_seconds: int = 120
    allowed_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".md", ".markdown", ".xlsx", ".xlsm", ".docx", ".pdf",
                                 ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"]
    )

    default_category: str = "General"
    default_language: str = "auto"
    ingestion_workers: int = 2

    # ---------- Knowledge layer ----------
    # Off isolates the knowledge arm completely: no query, no prompt change. The
    # regression gate runs the gold set both ways, so this is the switch that
    # proves the arm is neutral rather than merely believed to be.
    enable_knowledge_layer: bool = True
    # A collection of its own. Taught claims and document passages are never
    # ranked against each other in one vector space — that would let similarity
    # decide what the approval workflow exists to decide.
    qdrant_knowledge_collection: str = "alyafour_knowledge_items"
    # Below this a semantic hit is noise, not a match.
    knowledge_score_threshold: float = 0.45
    # How far approved knowledge may go when the documents have nothing.
    #   gated    — the current, production default. No document candidate means a
    #              refusal, and the knowledge arm is never consulted.
    #   eligible — experimental. ACTIVE knowledge in scope, above a higher
    #              similarity bar, with attribution and no unresolved conflict,
    #              may answer alone. Measured separately; not the default.
    knowledge_answer_mode: str = "gated"
    # Deliberately stricter than the supplementary floor: answering from taught
    # knowledge alone has no document to check it against.
    knowledge_only_threshold: float = 0.62
    # Detection runs on every message and never calls a model. Off disables the
    # suggestion entirely; it never affects how an answer is produced.
    enable_learning_signals: bool = True

    # ---------- Authentication ----------
    # How long a login lasts. Short enough that a forgotten open tab is not a standing
    # grant, long enough not to interrupt a working day.
    session_ttl_hours: int = 12
    session_cookie_name: str = "rag_session"
    # Set true behind HTTPS. Left false by default so the cookie still works on the
    # plain-HTTP localhost this runs on today.
    session_cookie_secure: bool = False
    # Bootstrap accounts, created by `python -m app.cli bootstrap` — never at import
    # time and never with a default password baked into the code.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    service_account_email: str = "service@local"
    service_account_password: str = ""

    # The web client is served from this app in production, so it is same-origin and
    # needs no CORS entry. These are for the Vite dev server during development.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    # Built single-page client. Mounted only when it exists, so the API runs unchanged
    # before the first `npm run build`.
    web_dist_dir: Path = BASE_DIR / "web" / "dist"

    # ---------- ERP integration (Phase 1) ----------
    # Off unless an operator turns it on. A machine-callable surface that appears the
    # moment the code is deployed is a surface nobody decided to expose.
    integration_enabled: bool = False
    # Root of every integration credential. Never stored in the database; without it no
    # credential can be minted or verified. Generate one with
    # `python -m app.cli integration-master-key`.
    integration_master_key: str = ""
    # Defaults to on. The request body carries supplier prices and purchase figures, and
    # a signature proves the body was not altered — it does not stop anyone reading it.
    integration_require_https: bool = True
    integration_timestamp_skew_seconds: int = 300
    integration_nonce_ttl_seconds: int = 600
    # The endpoint's own budget, well under the caller's 120s read timeout so the reply
    # is an orderly 408 rather than a dropped connection. Deliberately far below
    # `ollama_timeout`, which at 600s would let one call hold the model for ten minutes.
    integration_answer_budget_seconds: float = 90.0
    # Starting values, derived from measurement: one Ollama, 15–90s per answer, shared
    # with the people using the web interface. Re-tuned under real load, not guessed at.
    integration_rate_limit_per_minute: int = 6
    integration_rate_limit_burst: int = 10
    integration_max_concurrency: int = 2
    integration_queue_depth: int = 4
    integration_daily_quota: int = 500
    integration_idempotency_ttl_hours: int = 24
    integration_max_body_bytes: int = 65536

    @property
    def resolved_embedding_base_url(self) -> str:
        return self.embedding_base_url or self.ollama_base_url

    @field_validator("qdrant_api_key", mode="before")
    @classmethod
    def _blank_api_key_is_none(cls, value: object) -> object:
        return value or None

    @field_validator("web_search_allowed_domains", mode="before")
    @classmethod
    def _split_domains(cls, value):
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value or []

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def _split_extensions(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_extensions")
    @classmethod
    def _normalise_extensions(cls, value: list[str]) -> list[str]:
        return [ext if ext.startswith(".") else f".{ext}" for ext in (e.lower() for e in value)]

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_within_chunk(cls, value: int, info) -> int:
        chunk_size = info.data.get("chunk_size", 1400)
        if value >= chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Path(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
    return settings
