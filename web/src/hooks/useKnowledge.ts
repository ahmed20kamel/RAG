import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { knowledgeApi, type KnowledgeQuery, type KnowledgeTransition } from '@/services/knowledge'
import type { KnowledgeEditRequest, TeachRequest } from '@/types/knowledge'

export const knowledgeKeys = {
  all: ['knowledge'] as const,
  list: (query: KnowledgeQuery) => ['knowledge', 'list', query] as const,
  stats: ['knowledge', 'stats'] as const,
  detail: (id: string) => ['knowledge', 'detail', id] as const,
}

export function useKnowledgeList(query: KnowledgeQuery, enabled = true) {
  return useQuery({
    queryKey: knowledgeKeys.list(query),
    queryFn: () => knowledgeApi.list(query),
    enabled,
    placeholderData: (previous) => previous,
  })
}

export function useKnowledgeStats(enabled = true) {
  return useQuery({
    queryKey: knowledgeKeys.stats,
    queryFn: knowledgeApi.stats,
    enabled,
    staleTime: 30_000,
  })
}

export function useKnowledgeItem(id: string | undefined) {
  return useQuery({
    queryKey: knowledgeKeys.detail(id ?? ''),
    queryFn: () => knowledgeApi.get(id as string),
    enabled: Boolean(id),
  })
}

/**
 * Any write can move an item between the status tabs and change the tallies, so the
 * whole branch is refetched rather than the one list that happens to be on screen.
 */
function invalidateKnowledge(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: knowledgeKeys.all })
}

export function useTeachKnowledge() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: TeachRequest) => knowledgeApi.teach(body),
    onSuccess: () => invalidateKnowledge(client),
  })
}

export function useEditKnowledge() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: KnowledgeEditRequest }) =>
      knowledgeApi.edit(id, body),
    onSuccess: () => invalidateKnowledge(client),
  })
}

export function useKnowledgeTransition() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      action,
      reason,
    }: {
      id: string
      action: KnowledgeTransition
      reason?: string
    }) => knowledgeApi.transition(id, action, reason ?? ''),
    onSuccess: () => invalidateKnowledge(client),
  })
}
