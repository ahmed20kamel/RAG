import { api } from './client'
import type {
  AnswerExplanation,
  Candidate,
  CandidateAcceptRequest,
  CandidateDismissRequest,
  CandidateListResponse,
  CandidateState,
  CandidateStats,
  CorrectAnswerRequest,
} from '@/types/candidates'
import type { KnowledgeItem, KnowledgeType } from '@/types/knowledge'

export interface CandidateQuery {
  state?: CandidateState[]
  type?: KnowledgeType[]
  mine?: boolean
  limit?: number
  offset?: number
}

/**
 * `state` and `type` are repeated parameters rather than one comma-joined value, which
 * the shared `query` option cannot express — it takes scalars only — so the search
 * string is assembled here instead of widening the client for one caller.
 */
function searchString(query: CandidateQuery): string {
  const params = new URLSearchParams()
  for (const value of query.state ?? []) params.append('state', value)
  for (const value of query.type ?? []) params.append('type', value)
  // The server defaults `mine` to true; it is sent either way so asking for the whole
  // queue is an explicit `false` rather than an omission that silently narrows.
  params.set('mine', String(query.mine ?? true))
  params.set('limit', String(query.limit ?? 25))
  params.set('offset', String(query.offset ?? 0))
  return `?${params.toString()}`
}

export const candidatesApi = {
  list: (query: CandidateQuery = {}) =>
    api.get<CandidateListResponse>(`/api/candidates${searchString(query)}`),

  stats: () => api.get<CandidateStats>('/api/candidates/stats'),

  get: (id: string) => api.get<Candidate>(`/api/candidates/${id}`),

  /** Produces a PENDING proposal, never knowledge. The review still has to happen. */
  accept: (id: string, body: CandidateAcceptRequest) =>
    api.post<KnowledgeItem>(`/api/candidates/${id}/accept`, body),

  dismiss: (id: string, body: CandidateDismissRequest = {}) =>
    api.post<Candidate>(`/api/candidates/${id}/dismiss`, body),

  /** Raises a correction against an answer. Creates a candidate and nothing else. */
  correctAnswer: (body: CorrectAnswerRequest) =>
    api.post<Candidate>('/api/candidates/correct-answer', body),
}

export const explainApi = {
  why: (answerId: string) =>
    api.get<AnswerExplanation>(`/api/chat/answers/${answerId}/why`),
}
