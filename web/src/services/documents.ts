import { api, request } from './client'
import type {
  DocumentChunkPage,
  DocumentDetailResponse,
  DocumentListResponse,
  DocumentRaw,
  DocumentResponse,
  DocumentStatus,
  EntityResponse,
  LibraryStats,
  SectionResponse,
  UploadMetadata,
} from '@/types/api'

export interface DocumentQuery {
  status?: DocumentStatus
  category?: string
  project?: string
  search?: string
  limit?: number
  offset?: number
}

export const documentsApi = {
  list: (query: DocumentQuery = {}) =>
    api.get<DocumentListResponse>('/api/documents', {
      query: {
        status: query.status,
        category: query.category,
        project: query.project,
        search: query.search,
        limit: query.limit ?? 25,
        offset: query.offset ?? 0,
      },
    }),

  stats: () => api.get<LibraryStats>('/api/documents/stats'),

  categories: () => api.get<string[]>('/api/documents/categories'),

  projects: () => api.get<string[]>('/api/documents/projects'),

  // Chunks are paged separately, so opening a document does not transfer all of its text.
  detail: (id: string) =>
    api.get<DocumentDetailResponse>(`/api/documents/${id}`, { query: { include_chunks: false } }),

  chunks: (id: string, offset = 0, limit = 25) =>
    api.get<DocumentChunkPage>(`/api/documents/${id}/chunks`, { query: { offset, limit } }),

  sections: (id: string) => api.get<SectionResponse[]>(`/api/documents/${id}/sections`),

  entities: (id: string, kind?: string, limit = 400) =>
    api.get<EntityResponse[]>(`/api/documents/${id}/entities`, { query: { kind, limit } }),

  raw: (id: string) => api.get<DocumentRaw>(`/api/documents/${id}/raw`),

  reindex: (id: string) => api.post<DocumentResponse>(`/api/documents/${id}/reindex`),

  remove: (id: string) => api.delete<{ id: string; deleted: boolean; message: string }>(`/api/documents/${id}`),

  upload: (file: File, metadata: UploadMetadata = {}, signal?: AbortSignal) => {
    const form = new FormData()
    form.append('file', file)
    for (const [key, value] of Object.entries(metadata)) {
      if (value) form.append(key, value)
    }
    return request<DocumentResponse>('/api/documents/upload', {
      method: 'POST',
      body: form,
      signal,
    })
  },
}
