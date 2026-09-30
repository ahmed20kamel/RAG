/**
 * Mirrors `app/schemas/knowledge.py` and `app/core/knowledge.py`.
 *
 * The literals below are the Python enum *values*, not their member names — the wire
 * carries `in_review`, never `IN_REVIEW` — so a rename on either side surfaces here as
 * a compile error instead of an empty badge at runtime.
 */

export type KnowledgeType =
  | 'fact'
  | 'rule'
  | 'correction'
  | 'procedure'
  | 'terminology'
  | 'preference'

export type KnowledgeScope = 'user' | 'team' | 'department' | 'global'

export type KnowledgeStatus =
  | 'draft'
  | 'pending'
  | 'in_review'
  | 'approved'
  | 'active'
  | 'rejected'
  | 'archived'

/** Ordered so the factual types read together and the behavioural ones close the list. */
export const KNOWLEDGE_TYPES: KnowledgeType[] = [
  'fact',
  'correction',
  'procedure',
  'terminology',
  'rule',
  'preference',
]

/** Types that assert something about the world, so a document can contradict them. */
export const FACTUAL_TYPES: KnowledgeType[] = ['fact', 'correction', 'procedure', 'terminology']

/** Types that shape how an answer is written. These are never quoted as evidence. */
export const BEHAVIOURAL_TYPES: KnowledgeType[] = ['rule', 'preference']

export const KNOWLEDGE_SCOPES: KnowledgeScope[] = ['user', 'team', 'department', 'global']

export const KNOWLEDGE_STATUSES: KnowledgeStatus[] = [
  'draft',
  'pending',
  'in_review',
  'approved',
  'active',
  'rejected',
  'archived',
]

/** The decisions recorded in the audit trail — `ReviewDecision` in the backend. */
export const REVIEW_DECISIONS = [
  'submitted',
  'started_review',
  'approved',
  'activated',
  'rejected',
  'archived',
  'restored',
  'edited',
] as const

export type ReviewDecision = (typeof REVIEW_DECISIONS)[number]

export interface KnowledgeVersion {
  id: string
  version_no: number
  content: string
  source_text: string
  source_document_id: string | null
  source_section_id: string | null
  confidence: number
  explanation: string
  change_reason: string
  created_by: string | null
  created_at: string
}

export interface KnowledgeReview {
  id: string
  /** A `ReviewDecision` value, kept as a plain string because the server widens it. */
  decision: string
  from_status: string
  to_status: string
  reviewer_id: string | null
  reason: string
  created_at: string
}

export interface KnowledgeUsage {
  id: string
  question_hash: string
  user_id: string | null
  influence: string
  conflicted: boolean
  used_at: string
}

/** One item flattened with the version currently in force. */
export interface KnowledgeItem {
  id: string
  type: KnowledgeType
  scope: KnowledgeScope
  status: KnowledgeStatus
  content: string
  source_text: string
  source_document_id: string | null
  confidence: number
  version_no: number
  explanation: string
  tags: string[]

  owner_user_id: string | null
  owner_team_id: string | null
  department: string
  created_by: string | null
  created_by_name: string
  approved_by: string | null
  approved_by_name: string

  created_at: string
  updated_at: string
  approved_at: string | null
  activated_at: string | null
  archived_at: string | null

  /** Resolved server-side. The interface shows what these say and never re-derives them. */
  can_edit: boolean
  can_approve: boolean
  can_archive: boolean
}

export interface KnowledgeDetail extends KnowledgeItem {
  versions: KnowledgeVersion[]
  reviews: KnowledgeReview[]
  usages: KnowledgeUsage[]
  usage_count: number
}

export interface KnowledgeListResponse {
  total: number
  items: KnowledgeItem[]
}

export interface KnowledgeStats {
  total: number
  by_status: Record<string, number>
  by_type: Record<string, number>
  by_scope: Record<string, number>
  pending_review: number
  active: number
}

export interface TeachRequest {
  type: KnowledgeType
  content: string
  scope?: KnowledgeScope
  source_text?: string
  source_document_id?: string | null
  source_section_id?: string | null
  confidence?: number
  explanation?: string
  tags?: string[]
  team_id?: string | null
  department?: string
}

export interface KnowledgeEditRequest {
  content: string
  change_reason?: string
  source_text?: string | null
  confidence?: number | null
  explanation?: string | null
}
