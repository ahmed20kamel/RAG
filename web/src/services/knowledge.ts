import { api } from './client'
import type {
  KnowledgeDetail,
  KnowledgeEditRequest,
  KnowledgeItem,
  KnowledgeListResponse,
  KnowledgeScope,
  KnowledgeStats,
  KnowledgeStatus,
  KnowledgeType,
  TeachRequest,
} from '@/types/knowledge'

export interface KnowledgeQuery {
  type?: KnowledgeType[]
  scope?: KnowledgeScope[]
  status?: KnowledgeStatus[]
  q?: string
  tag?: string
  mine?: boolean
  limit?: number
  offset?: number
}

/** The status changes the backend exposes; each one is its own endpoint. */
export type KnowledgeTransition =
  | 'submit'
  | 'review'
  | 'approve'
  | 'activate'
  | 'reject'
  | 'archive'
  | 'restore'

/**
 * `type`, `scope` and `status` are repeated parameters rather than one comma-joined
 * value, which the shared `query` option cannot express — it takes scalars only — so
 * the search string is assembled here instead of widening the client for one caller.
 */
function searchString(query: KnowledgeQuery): string {
  const params = new URLSearchParams()
  for (const value of query.type ?? []) params.append('type', value)
  for (const value of query.scope ?? []) params.append('scope', value)
  for (const value of query.status ?? []) params.append('status', value)
  if (query.q) params.set('q', query.q)
  if (query.tag) params.set('tag', query.tag)
  if (query.mine) params.set('mine', 'true')
  params.set('limit', String(query.limit ?? 25))
  params.set('offset', String(query.offset ?? 0))
  return `?${params.toString()}`
}

export const knowledgeApi = {
  list: (query: KnowledgeQuery = {}) =>
    api.get<KnowledgeListResponse>(`/api/knowledge${searchString(query)}`),

  stats: () => api.get<KnowledgeStats>('/api/knowledge/stats'),

  get: (id: string) => api.get<KnowledgeDetail>(`/api/knowledge/${id}`),

  teach: (body: TeachRequest) => api.post<KnowledgeItem>('/api/knowledge', body),

  edit: (id: string, body: KnowledgeEditRequest) =>
    api.patch<KnowledgeItem>(`/api/knowledge/${id}`, body),

  /** Every decision carries a reason; the backend refuses an empty one on a rejection. */
  transition: (id: string, action: KnowledgeTransition, reason = '') =>
    api.post<KnowledgeItem>(`/api/knowledge/${id}/${action}`, { reason }),

  submit: (id: string, reason = '') => knowledgeApi.transition(id, 'submit', reason),
  review: (id: string, reason = '') => knowledgeApi.transition(id, 'review', reason),
  approve: (id: string, reason = '') => knowledgeApi.transition(id, 'approve', reason),
  activate: (id: string, reason = '') => knowledgeApi.transition(id, 'activate', reason),
  reject: (id: string, reason = '') => knowledgeApi.transition(id, 'reject', reason),
  archive: (id: string, reason = '') => knowledgeApi.transition(id, 'archive', reason),
  restore: (id: string, reason = '') => knowledgeApi.transition(id, 'restore', reason),
}
