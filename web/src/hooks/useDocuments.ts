import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { documentsApi, type DocumentQuery } from '@/services/documents'
import { PIPELINE_STAGES, type DocumentResponse, type DocumentStatus } from '@/types/api'

export const documentKeys = {
  all: ['documents'] as const,
  list: (query: DocumentQuery) => ['documents', 'list', query] as const,
  stats: ['documents', 'stats'] as const,
  categories: ['documents', 'categories'] as const,
  projects: ['documents', 'projects'] as const,
  folders: ['documents', 'folders'] as const,
  detail: (id: string) => ['documents', 'detail', id] as const,
  chunks: (id: string, offset: number) => ['documents', 'chunks', id, offset] as const,
  sections: (id: string) => ['documents', 'sections', id] as const,
  entities: (id: string, kind?: string) => ['documents', 'entities', id, kind ?? 'all'] as const,
  raw: (id: string) => ['documents', 'raw', id] as const,
}

/** Statuses that mean the pipeline is still working, so the view should keep polling. */
//
// Derived from the pipeline order rather than listed by hand. The hand-written list
// left out 'validating', so a document first seen in that stage read as finished:
// polling stopped, and the upload page reported a failure for a file that went on to
// index normally.
const IN_FLIGHT: DocumentStatus[] = PIPELINE_STAGES.filter((stage) => stage !== 'completed')

export function isInFlight(status: DocumentStatus): boolean {
  return IN_FLIGHT.includes(status)
}

export function useDocumentList(query: DocumentQuery) {
  return useQuery({
    queryKey: documentKeys.list(query),
    queryFn: () => documentsApi.list(query),
    // While anything is mid-pipeline the list refreshes on its own; once everything has
    // settled the polling stops, so an idle tab costs nothing.
    refetchInterval: (q) =>
      q.state.data?.items.some((doc) => isInFlight(doc.status)) ? 2500 : false,
    placeholderData: (previous) => previous,
  })
}

export function useLibraryStats() {
  return useQuery({
    queryKey: documentKeys.stats,
    queryFn: documentsApi.stats,
    refetchInterval: (q) => ((q.state.data?.processing ?? 0) > 0 ? 2500 : 30_000),
  })
}

export function useCategories() {
  return useQuery({
    queryKey: documentKeys.categories,
    queryFn: documentsApi.categories,
    staleTime: 60_000,
  })
}

export function useProjects() {
  return useQuery({
    queryKey: documentKeys.projects,
    queryFn: documentsApi.projects,
    staleTime: 60_000,
  })
}

export function useFolders() {
  return useQuery({
    queryKey: documentKeys.folders,
    queryFn: documentsApi.folders,
    // Folders appear as the organizer sorts new uploads, a little after they index.
    refetchInterval: 15_000,
  })
}

export function useDocument(id: string | undefined) {
  return useQuery({
    queryKey: documentKeys.detail(id ?? ''),
    queryFn: () => documentsApi.detail(id as string),
    enabled: Boolean(id),
    refetchInterval: (q) => (q.state.data && isInFlight(q.state.data.status) ? 2000 : false),
  })
}

export function useDocumentChunks(id: string | undefined, offset: number, limit = 20, enabled = true) {
  return useQuery({
    queryKey: documentKeys.chunks(id ?? '', offset),
    queryFn: () => documentsApi.chunks(id as string, offset, limit),
    enabled: Boolean(id) && enabled,
    placeholderData: (previous) => previous,
  })
}

export function useDocumentSections(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: documentKeys.sections(id ?? ''),
    queryFn: () => documentsApi.sections(id as string),
    enabled: Boolean(id) && enabled,
  })
}

export function useDocumentEntities(id: string | undefined, kind?: string, enabled = true) {
  return useQuery({
    queryKey: documentKeys.entities(id ?? '', kind),
    queryFn: () => documentsApi.entities(id as string, kind),
    enabled: Boolean(id) && enabled,
  })
}

export function useDocumentRaw(id: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: documentKeys.raw(id ?? ''),
    queryFn: () => documentsApi.raw(id as string),
    enabled: Boolean(id) && enabled,
    staleTime: 5 * 60_000,
  })
}

function invalidateLibrary(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: documentKeys.all })
}

export function useReindexDocument() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => documentsApi.reindex(id),
    onSuccess: () => invalidateLibrary(client),
  })
}

export function useDeleteDocument() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => documentsApi.remove(id),
    // The row disappears immediately; a failure re-runs the query and brings it back.
    onMutate: async (id: string) => {
      await client.cancelQueries({ queryKey: documentKeys.all })
      const snapshot = client.getQueriesData({ queryKey: ['documents', 'list'] })
      client.setQueriesData<{ total: number; items: DocumentResponse[] }>(
        { queryKey: ['documents', 'list'] },
        (current) =>
          current
            ? { total: Math.max(0, current.total - 1), items: current.items.filter((d) => d.id !== id) }
            : current,
      )
      return { snapshot }
    },
    onError: (_error, _id, context) => {
      context?.snapshot.forEach(([key, value]) => client.setQueryData(key, value))
    },
    onSettled: () => invalidateLibrary(client),
  })
}
