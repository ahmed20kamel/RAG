/**
 * Mirrors `app/schemas/candidates.py` and the `AnswerExplanation` half of
 * `app/schemas/chat.py`.
 *
 * As in `knowledge.ts`, the literals are the Python enum *values* — the wire carries
 * `offered`, never `OFFERED` — so a rename on either side surfaces as a compile error
 * rather than an empty badge at runtime.
 */

import type { KnowledgeScope, KnowledgeType } from './knowledge'

/** `CandidateState` in `app/services/candidate_service.py`. Only `accepted` proposes. */
export type CandidateState = 'offered' | 'accepted' | 'dismissed' | 'rejected'

/** Ordered as the queue is read: what is still open first, then how it was closed. */
export const CANDIDATE_STATES: CandidateState[] = ['offered', 'accepted', 'dismissed', 'rejected']

/**
 * What detection noticed, returned beside an answer as an offer.
 *
 * It is a suggestion, not a change: nothing reaches an answer until the person accepts
 * it and — unless it is a personal preference — a reviewer approves the proposal.
 */
export interface LearningSignal {
  candidate_id: string
  type: KnowledgeType
  signal: string
  suggested_content: string
  confidence: number
  proposed_scope: KnowledgeScope
  needs_approval: boolean
}

export interface Candidate {
  id: string
  detected_type: KnowledgeType
  signal: string
  /** What the person actually typed, kept beside the tidied suggestion. */
  raw_text: string
  suggested_content: string
  confidence: number
  state: CandidateState
  proposed_scope: KnowledgeScope

  conversation_id: string
  answer_id: string
  corrects_item_id: string | null
  promoted_item_id: string | null

  user_id: string | null
  proposer_name: string
  resolution_reason: string
  resolved_by: string | null

  created_at: string
  resolved_at: string | null

  /** Resolved server-side. The interface shows what this says and never re-derives it. */
  can_resolve: boolean
}

export interface CandidateListResponse {
  total: number
  items: Candidate[]
}

export interface CandidateStats {
  total: number
  by_state: Record<string, number>
  by_type: Record<string, number>
  open_for_me: number
}

/** The wording, the type and the reach may all be corrected before accepting. */
export interface CandidateAcceptRequest {
  content?: string | null
  type?: KnowledgeType | null
  scope?: KnowledgeScope | null
  source_text?: string
  explanation?: string
  tags?: string[]
}

export interface CandidateDismissRequest {
  reason?: string
  /** A rejection is a dismissal the record keeps apart: declined, not merely ignored. */
  rejected?: boolean
}

export interface CorrectAnswerRequest {
  answer_id: string
  correction: string
  source_text?: string
  explanation?: string
  corrects_item_id?: string | null
}

/* Why this answer ---------------------------------------------------------- */

export interface DocumentEvidence {
  citation: number
  document_id: string
  filename: string
  section: string
  section_id: string
  score: number
  tier: string
}

/** One approved item that reached the prompt — `KnowledgeReference` on the wire. */
export interface KnowledgeContribution {
  item_id: string
  version_id: string
  type: string
  scope: string
  content: string
  source_text: string
  source_document_id: string | null
  confidence: number
  version_no: number
  /** offered | applied | stated — how far it got into the answer. */
  influence: string
  conflicted: boolean
  score: number
  retrieval: string
  answer_id: string
}

export interface ConflictSide {
  ref: string
  /** document | knowledge — which half of the answer this value came from. */
  kind: string
  values: string[]
  citation: number
  item_id: string
  label: string
}

export interface AnswerConflict {
  description: string
  resolved: boolean
  /** The recorded basis for the decision, or `unresolved` when none was taken. */
  basis: string
  winner: string
  left: ConflictSide
  right: ConflictSide
  shared_terms: string[]
}

/**
 * What one answer was built from.
 *
 * Four separate lists on purpose: merging a filed passage with a colleague's claim, or
 * a rule with a fact, would hide the distinction the reader needs in order to judge the
 * answer. No reasoning is recorded or returned — only evidence and decisions.
 */
export interface AnswerExplanation {
  answer_id: string
  question: string
  model: string
  created_at: string | null

  document_evidence: DocumentEvidence[]
  knowledge_used: KnowledgeContribution[]
  policies_applied: KnowledgeContribution[]
  conflicts: AnswerConflict[]

  conflict_count: number
  unresolved_conflicts: number
  knowledge_layer_enabled: boolean
}
