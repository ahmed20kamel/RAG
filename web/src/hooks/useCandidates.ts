import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { candidatesApi, explainApi, type CandidateQuery } from '@/services/candidates'
import { knowledgeKeys } from '@/hooks/useKnowledge'
import type {
  CandidateAcceptRequest,
  CandidateDismissRequest,
  CorrectAnswerRequest,
} from '@/types/candidates'

export const candidateKeys = {
  all: ['candidates'] as const,
  list: (query: CandidateQuery) => ['candidates', 'list', query] as const,
  stats: ['candidates', 'stats'] as const,
  detail: (id: string) => ['candidates', 'detail', id] as const,
  why: (answerId: string) => ['candidates', 'why', answerId] as const,
}

export function useCandidateList(query: CandidateQuery, enabled = true) {
  return useQuery({
    queryKey: candidateKeys.list(query),
    queryFn: () => candidatesApi.list(query),
    enabled,
    placeholderData: (previous) => previous,
  })
}

export function useCandidateStats(enabled = true) {
  return useQuery({
    queryKey: candidateKeys.stats,
    queryFn: candidatesApi.stats,
    enabled,
    staleTime: 30_000,
  })
}

export function useCandidate(id: string | undefined) {
  return useQuery({
    queryKey: candidateKeys.detail(id ?? ''),
    queryFn: () => candidatesApi.get(id as string),
    enabled: Boolean(id),
  })
}

/**
 * Any resolution moves a suggestion between the state tabs and changes the tallies, so
 * the whole branch is refetched rather than the one list that happens to be on screen.
 */
function invalidateCandidates(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: candidateKeys.all })
}

export function useAcceptCandidate() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CandidateAcceptRequest }) =>
      candidatesApi.accept(id, body),
    onSuccess: () => {
      invalidateCandidates(client)
      // Accepting files a PENDING proposal, so the knowledge queue has grown too.
      void client.invalidateQueries({ queryKey: knowledgeKeys.all })
    },
  })
}

export function useDismissCandidate() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CandidateDismissRequest }) =>
      candidatesApi.dismiss(id, body),
    onSuccess: () => invalidateCandidates(client),
  })
}

export function useCorrectAnswer() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: CorrectAnswerRequest) => candidatesApi.correctAnswer(body),
    onSuccess: () => invalidateCandidates(client),
  })
}

/**
 * The record behind one answer. It never changes once written, so it is fetched only
 * when something actually asks for it and then kept.
 */
export function useWhyThisAnswer(answerId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: candidateKeys.why(answerId ?? ''),
    queryFn: () => explainApi.why(answerId as string),
    enabled: enabled && Boolean(answerId),
    staleTime: Number.POSITIVE_INFINITY,
  })
}
