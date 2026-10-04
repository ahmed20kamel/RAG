"""Composition root: builds and wires every service exactly once."""

from __future__ import annotations

import logging

from app.config import Settings
from app.parsers.base import ParserRegistry
from app.parsers.docx_parser import DocxParser
from app.parsers.markdown_parser import MarkdownParser
from app.parsers.pdf_parser import PdfParser
from app.parsers.xlsx_parser import XlsxParser
from app.services.answer_validation import AnswerValidator
from app.services.auth_service import AuthService
from app.services.chunking import HeadingAwareChunker
from app.services.context_builder import ContextBuilder
from app.services.document_scope import DocumentScope
from app.services.document_service import DocumentService
from app.services.embeddings import OllamaEmbeddingClient
from app.services.completion import CompletionEngine
from app.services.contract_builder import AnswerContractBuilder
from app.services.coverage import CoverageValidator
from app.services.evidence_planner import EvidencePlanner
from app.services.candidate_service import CandidateService
from app.services.conflict_detector import ConflictDetector
from app.services.signal_detector import SignalDetector
from app.services.knowledge_arm import KnowledgeArm
from app.services.knowledge_index import KnowledgeVectorIndex
from app.services.knowledge_service import KnowledgeService
from app.services.fact_sheet import FactSheetBuilder
from app.services.entities import EntityExtractor
from app.services.ingestion import IngestionPipeline
from app.services.integration_auth import IntegrationAuthenticator
from app.services.integration_limits import (
    ConcurrencyGate,
    IdempotencyStore,
    RateLimiter,
)
from app.services.keyword_index import KeywordIndex
from app.services.knowledge_store import KnowledgeStore
from app.services.llm import OllamaLLMClient
from app.services.metrics import MetricsRecorder, Thresholds
from app.services.query_analysis import QueryAnalyzer
from app.services.query_rewrite import SynonymLexicon, learned_terms_from_db
from app.services.rag_service import RagService
from app.services.learning_loop import AnswerMemory, RephraseLearner
from app.services.organizer import DocumentOrganizer
from app.services.table_assist import RagflowTableAssist
from app.services.reranking import CrossEncoderReranker, FeatureReranker
from app.services.retriever import HybridRetriever
from app.services.structure import DocumentStructureAnalyzer
from app.services.summaries import SectionSummarizer
from app.services.vector_store import QdrantVectorStore
from app.services.web_search import WebSearchService

logger = logging.getLogger(__name__)


class Container:
    @staticmethod
    def _build_reranker(settings: Settings):
        """The configured reranker. "cross" without its model degrades to features.

        The model is not loaded here — it loads on the first question — so a missing
        or broken model shows up in the health report and the log, not as a failed start.
        """
        if settings.reranker.strip().lower() != "cross":
            return FeatureReranker()
        from pathlib import Path

        from app.services.cross_encoder import CrossEncoderScorer

        model_dir = Path(settings.reranker_model_dir)
        if not model_dir.is_absolute():
            model_dir = Path(__file__).resolve().parent.parent / model_dir
        scorer = CrossEncoderScorer(
            model_dir=model_dir,
            max_tokens=settings.reranker_max_tokens,
            threads=settings.reranker_threads,
        )
        return CrossEncoderReranker(
            scorer,
            weight=settings.reranker_weight,
            top_n=settings.reranker_top_n,
            budget_ms=settings.reranker_budget_ms,
        )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.auth_service = AuthService(session_ttl_hours=settings.session_ttl_hours)

        # The ERP integration surface. Built whatever the setting says, so the wiring is
        # identical either way; switched off, the endpoint refuses before it reaches any
        # of this. The rate limiter and the concurrency gate hold process-local state
        # and are therefore built once here rather than per request — a limiter rebuilt
        # for each call enforces nothing.
        self.integration_auth = IntegrationAuthenticator(
            master_key=settings.integration_master_key,
            require_https=settings.integration_require_https,
            skew_seconds=settings.integration_timestamp_skew_seconds,
            nonce_ttl_seconds=settings.integration_nonce_ttl_seconds,
        )
        self.integration_rate_limiter = RateLimiter()
        self.integration_concurrency = ConcurrencyGate()
        self.integration_idempotency = IdempotencyStore(
            ttl_hours=settings.integration_idempotency_ttl_hours
        )

        # Phase 2 formats register here; nothing else in the app changes.
        # Order matters only for readability; each parser claims its own extensions.
        self.parser_registry = ParserRegistry([
            MarkdownParser(settings.max_heading_depth),
            XlsxParser(),
            DocxParser(settings.max_heading_depth),
            PdfParser(enable_ocr=settings.enable_ocr),
        ])

        self.chunker = HeadingAwareChunker(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            min_chunk_size=settings.min_chunk_size,
        )
        self.embedder = OllamaEmbeddingClient(
            base_url=settings.resolved_embedding_base_url,
            model=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            timeout=settings.embedding_timeout,
            keep_alive=settings.embedding_keep_alive,
        )
        # Constructed whatever the setting says, so the wiring is identical either way;
        # switched off, it searches nothing and the answer pipeline never reaches it.
        self.web_search = WebSearchService(
            enabled=settings.web_search_enabled,
            max_results=settings.web_search_max_results,
            timeout=settings.web_search_timeout,
            allowed_domains=tuple(settings.web_search_allowed_domains),
        )

        self.knowledge_index = KnowledgeVectorIndex(
            url=settings.qdrant_url,
            collection=settings.qdrant_knowledge_collection,
            api_key=settings.qdrant_api_key,
            timeout=settings.qdrant_timeout,
        )
        self.knowledge_service = KnowledgeService(index=self.knowledge_index, embedder=self.embedder)
        self.knowledge_arm = KnowledgeArm()
        self.conflict_detector = ConflictDetector()
        self.signal_detector = SignalDetector()
        self.candidate_service = CandidateService(
            detector=self.signal_detector, knowledge=self.knowledge_service
        )

        self.vector_store = QdrantVectorStore(
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            api_key=settings.qdrant_api_key,
            timeout=settings.qdrant_timeout,
        )
        self.llm = OllamaLLMClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            temperature=settings.ollama_temperature,
            num_ctx=settings.ollama_num_ctx,
            timeout=settings.ollama_timeout,
            think=settings.ollama_think,
            keep_alive=settings.ollama_keep_alive,
        )

        # Document intelligence — all deterministic, all at ingestion time.
        self.structure = DocumentStructureAnalyzer()
        self.entity_extractor = EntityExtractor()
        self.summarizer = SectionSummarizer()
        self.knowledge = KnowledgeStore()
        self.keyword_index = KeywordIndex()

        self.pipeline = IngestionPipeline(
            registry=self.parser_registry,
            chunker=self.chunker,
            embedder=self.embedder,
            store=self.vector_store,
            structure=self.structure,
            entity_extractor=self.entity_extractor,
            summarizer=self.summarizer,
            knowledge=self.knowledge,
            keyword_index=self.keyword_index,
            table_assist=RagflowTableAssist(
                base_url=settings.ragflow_url,
                api_key=settings.ragflow_api_key,
                dataset=settings.table_assist_dataset,
                timeout=settings.table_assist_timeout,
                enabled=settings.table_assist_enabled,
            ),
            organizer=DocumentOrganizer(llm=self.llm, enabled=settings.auto_organize_enabled),
        )
        self.document_service = DocumentService(
            settings=settings,
            pipeline=self.pipeline,
            registry=self.parser_registry,
            store=self.vector_store,
            knowledge=self.knowledge,
            keyword_index=self.keyword_index,
        )

        # Built-in synonym groups, plus approved global terminology read from the
        # knowledge base and refreshed every minute — so a term a reviewer approves
        # reaches search without a restart.
        self.query_analyzer = QueryAnalyzer(
            lexicon=SynonymLexicon(
                learned=learned_terms_from_db if settings.enable_knowledge_layer else None
            )
        )
        self.reranker = self._build_reranker(settings)
        self.retriever = HybridRetriever(
            embedder=self.embedder,
            store=self.vector_store,
            keyword_index=self.keyword_index,
            knowledge=self.knowledge,
            reranker=self.reranker,
            top_k=settings.top_k,
            candidate_pool=settings.candidate_pool,
            score_threshold=settings.score_threshold,
            vector_score_floor=settings.vector_score_floor,
            min_rerank_score=settings.min_rerank_score,
            max_expanded_candidates=settings.max_expanded_candidates,
            wide_top_k=settings.wide_top_k,
            min_covered_terms=settings.min_covered_terms,
            timeline_sections=settings.timeline_sections,
            reserved_semantic_slots=settings.reserved_semantic_slots,
            enable_keyword_search=settings.enable_keyword_search,
            enable_entity_retrieval=settings.enable_entity_retrieval,
            enable_expansion=settings.enable_expansion,
        )
        self.metrics = MetricsRecorder(
            reranker_name=self.reranker.name,
            thresholds=Thresholds(
                p95_ms=settings.monitor_p95_ms,
                error_rate=settings.monitor_error_rate,
                refusal_rate=settings.monitor_refusal_rate,
            ),
        )
        self.rephrase_learner = RephraseLearner(
            analyzer=self.query_analyzer,
            keyword_index=self.keyword_index,
            knowledge_service=self.knowledge_service,
        )
        self.rag_service = RagService(
            analyzer=self.query_analyzer,
            retriever=self.retriever,
            context_builder=ContextBuilder(
                max_context_chars=settings.max_context_chars,
                primary_count=settings.primary_evidence_count,
                narrow_context_chars=settings.narrow_context_chars,
                max_related_blocks=settings.max_related_blocks,
            ),
            llm=self.llm,
            validator=AnswerValidator(),
            knowledge=self.knowledge,
            fact_sheet=FactSheetBuilder(max_facts=settings.max_facts_in_prompt),
            contracts=AnswerContractBuilder(),
            planner=EvidencePlanner(),
            coverage=CoverageValidator(),
            completion=CompletionEngine(self.llm, max_passes=settings.max_completion_passes),
            completeness_retry=settings.completeness_retry,
            knowledge_service=self.knowledge_service,
            knowledge_arm=self.knowledge_arm,
            knowledge_index=self.knowledge_index,
            web_search=self.web_search,
            metrics=self.metrics,
            answer_memory=AnswerMemory() if settings.answer_memory_enabled else None,
            document_scope=DocumentScope(),
            redact_sensitive=settings.redact_sensitive,
            conflicts=self.conflict_detector,
            embedder=self.embedder,
            knowledge_score_threshold=settings.knowledge_score_threshold,
            knowledge_answer_mode=settings.knowledge_answer_mode,
            knowledge_only_threshold=settings.knowledge_only_threshold,
            enable_knowledge_layer=settings.enable_knowledge_layer,
        )

    def warmup(self) -> None:
        """Prepare indexes ahead of the first question. Non-fatal if a service is down."""
        try:
            self.vector_store.ensure_collection(self.embedder.dimension)
        except Exception as exc:  # noqa: BLE001 - startup must not crash on a cold dependency
            logger.warning("Vector store warmup skipped: %s", exc)
        try:
            # Its own collection, created next to the document one and never merged.
            self.knowledge_index.ensure_collection(self.embedder.dimension)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Knowledge index warmup skipped: %s", exc)
        try:
            self.document_service.remove_orphans()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Leftover cleanup skipped: %s", exc)
        try:
            self.keyword_index.rebuild()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Keyword index warmup skipped: %s", exc)

    def shutdown(self) -> None:
        self.document_service.shutdown()
