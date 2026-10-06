/**
 * Mirrors the backend's Pydantic schemas.
 *
 * `app/schemas/chat.py` and `app/schemas/document.py` are the source of truth; these
 * types exist so a rename on either side shows up as a compile error rather than as an
 * undefined value at runtime.
 */

import type { LearningSignal } from './candidates'

export type DocumentStatus =
  | 'uploaded'
  | 'validating'
  | 'parsing'
  | 'analyzing'
  | 'chunking'
  | 'embedding'
  | 'indexing'
  | 'completed'
  | 'failed'
  | 'unsupported_format'
  | 'failed_extraction'
  | 'failed_parsing'
  | 'ocr_required'
  | 'failed_embedding'

/** Every status before `completed`, in the order the pipeline moves through them. */
export const PIPELINE_STAGES: DocumentStatus[] = [
  'uploaded',
  'validating',
  'parsing',
  'analyzing',
  'chunking',
  'embedding',
  'indexing',
  'completed',
]

/**
 * The statuses that mean the document stopped and will not resume on its own.
 *
 * Kept as a set rather than a `=== 'failed'` check in each component: the backend now
 * distinguishes an unreadable scan from a malformed file from a rejected format, and a
 * component testing only for `'failed'` would render every one of them as if the
 * document were still working its way through the pipeline.
 */
export const TERMINAL_FAILURES: ReadonlySet<DocumentStatus> = new Set([
  'failed',
  'unsupported_format',
  'failed_extraction',
  'failed_parsing',
  'ocr_required',
  'failed_embedding',
])

export type EvidenceTier = 'primary' | 'supporting' | 'related'

export interface SourceReference {
  citation: number
  document_id: string
  filename: string
  title: string
  section: string
  section_path: string
  section_id: string
  heading: string
  parent_section: string
  chunk_id: string
  score: number
  vector_score: number | null
  keyword_score: number | null
  version: string
  category: string
  tier: EvidenceTier
  origin: string
  excerpt: string
}

/** An external page an answer rested on. Never merged with `SourceReference`. */
export interface WebSource {
  citation: number
  title: string
  url: string
  domain: string
  snippet: string
  /** A government body, university or standards organisation. Ranking only. */
  authoritative: boolean
}

export interface SourceFact {
  label: string
  value: string
  kind: string
  citation: number
  primary: boolean
  section_heading: string
}

export interface CoverageReport {
  sections_retrieved: number
  documents_retrieved: number
  facts_extracted: number
  facts_used: number
  expected_entities: string[]
  answered_entities: string[]
  missing_entities: string[]
  completeness_score: number
  passes: number
}

export interface AnswerValidation {
  complete: boolean
  cited_sources: number[]
  uncited_evidence: number[]
  unsupported_values: string[]
  unanswered_parts: string[]
  omitted_facts: string[]
  conflicts: string[]
  warnings: string[]
  expanded: boolean
}

/** One click that answers a clarification: the complete question to send. */
export interface ChatChoice {
  label: string
  question: string
}

export interface QueryPlan {
  intent: string
  language: string
  keywords: string[]
  identifiers: string[]
  multi_part: boolean
  wide_retrieval: boolean
  exhaustive: boolean
  role_specific: boolean
  high_risk: boolean
  requirements: string[]
  /** A compound question's parts as asked; each was searched on its own. */
  parts?: string[]
  /** Colloquial forms mapped and synonyms added for search, as "from→to" notes. */
  rewrites?: string[]
}

export interface ChatRequest {
  question: string
  top_k?: number | null
  category?: string | null
  document_ids?: string[] | null
  /** Lets the server read a follow-up against the question before it. */
  conversation_id?: string | null
  /** Regenerate: answer afresh rather than from memory. */
  fresh?: boolean
  /** Deep thinking: the model reasons before it answers and before it reviews. */
  deep?: boolean
  /** Words read from an attached picture, and from the part marked on it. */
  image_text?: string | null
  image_marked?: string | null
  image_vision?: string | null
}



export interface ChatResponse {
  answer: string
  grounded: boolean
  sources: SourceReference[]
  retrieved_chunks: number
  model: string
  plan: QueryPlan | null
  validation: AnswerValidation | null
  facts: SourceFact[]
  /** External pages, kept apart so nothing can present one as internal. */
  web_sources: WebSource[]
  /** 'internal' | 'web' | 'none' — where this answer actually came from. */
  answer_source: string
  /** Present when the reply asks which file was meant. */
  choices?: ChatChoice[]
  coverage: CoverageReport | null
  /** Identifies the stored trace behind this answer, for "why this answer?". */
  answer_id: string
  /** Files made for this reply — PDF, Word or Excel asked for in the chat. */
  files?: ChatFile[]
  /** Set when the message read as teaching. An offer to the person, not a change. */
  learning_signal: LearningSignal | null
  timings_ms: Record<string, number>
}

/** Stage names reported by `POST /api/chat/stream`, in pipeline order. */
export type ChatStage =
  | 'retrieving'
  | 'assembling'
  | 'extracting'
  | 'planning'
  | 'generating'
  | 'verifying'
  | 'completing'

export type ChatStreamEvent =
  | { type: 'stage'; stage: ChatStage }
  | { type: 'result'; response: ChatResponse }
  | { type: 'error'; detail: string }
  /** Keep-alive while the model writes; carries nothing. */
  | { type: 'ping' }
  /** Questions answered before this one, and the estimated seconds until its answer. */
  | { type: 'load'; ahead: number; wait_seconds: number }

export interface ChatLoad {
  ahead: number
  waitSeconds: number
  /** When the estimate was received, so the interface can count it down. */
  at: number
}

export interface DocumentResponse {
  id: string
  filename: string
  title: string
  category: string
  /** The project folder it was uploaded from, and its folder inside it; empty for a single file. */
  project: string
  folder: string
  source: string
  version: string
  doc_date: string
  language: string
  /** The detected format: markdown | pdf | docx | xlsx. */
  file_type: string
  status: DocumentStatus
  error_message: string | null
  chunk_count: number
  char_count: number
  size_bytes: number
  extra_metadata: Record<string, unknown>
  uploaded_at: string
  updated_at: string
  indexed_at: string | null
}

/** One project / document-type folder of the library and how many documents it holds. */
export interface FolderEntry {
  project: string
  category: string
  count: number
}

export interface DocumentListResponse {
  total: number
  items: DocumentResponse[]
}

export interface DocumentChunkPreview {
  chunk_id: string
  index: number
  heading: string
  section: string
  char_count: number
  content: string
}

export interface DocumentDetailResponse extends DocumentResponse {
  chunks: DocumentChunkPreview[]
}

export interface DocumentChunkPage {
  total: number
  offset: number
  items: DocumentChunkPreview[]
}

export interface DocumentRaw {
  id: string
  filename: string
  content: string
  char_count: number
}

export interface SectionResponse {
  section_id: string
  heading: string
  path: string
  level: number
  parent_id: string | null
  child_ids: string[]
  summary: string
  terms: string[]
  has_table: boolean
  has_list: boolean
  has_code: boolean
  char_count: number
}

export interface EntityResponse {
  kind: string
  value: string
  label: string
  section_id: string
  context: string
}

export interface LibraryStats {
  documents: number
  by_status: Partial<Record<DocumentStatus, number>>
  processing: number
  failed: number
  chunks: number
  characters: number
  bytes: number
  categories: number
  last_ingested_at: string | null
  last_uploaded_at: string | null
}

export interface ServiceHealth {
  reachable: boolean
  url: string
  model?: string
  model_available?: boolean
  collection?: string
  collection_exists?: boolean
  points?: number
  error?: string
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  ollama: ServiceHealth
  embeddings: ServiceHealth
  qdrant: ServiceHealth
  keyword_index: { chunks: number; terms: number }
  knowledge_index: { sections: number; entities: number; chunks: number }
  embedding_cache: { hits: number; misses: number }
}

export interface EffectiveConfig {
  ollama_base_url: string
  ollama_model: string
  ollama_num_ctx: number
  ollama_think: boolean
  embedding_base_url: string
  embedding_model: string
  qdrant_collection: string
  top_k: number
  wide_top_k: number
  candidate_pool: number
  score_threshold: number
  vector_score_floor: number
  min_rerank_score: number
  max_context_chars: number
  chunk_size: number
  chunk_overlap: number
  reranker: string
  hybrid: {
    keyword_search: boolean
    entity_retrieval: boolean
    expansion: boolean
  }
  supported_extensions: string[]
}

export interface UploadMetadata {
  title?: string
  category?: string
  source?: string
  version?: string
  date?: string
  language?: string
  /** From a folder upload. */
  project?: string
  folder?: string
}

/** A region marked on a picture, as fractions of its width and height. */
export interface PictureMark {
  x: number
  y: number
  w: number
  h: number
}

/** A picture attached to a question: what it showed, and what was read from it. */
export interface ChatPicture {
  /** A small JPEG of the picture with its marks, for the conversation. */
  thumbnail: string
  text: string
  marked: string
  /** What the vision model understood the picture to show. */
  vision?: string
  hasText: boolean
}

/** A file made for the reader in a reply. */
export interface ChatFile {
  id: string
  name: string
  format: 'pdf' | 'docx' | 'xlsx' | string
  size: number
}
