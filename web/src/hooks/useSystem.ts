import { useQuery } from '@tanstack/react-query'
import { systemApi } from '@/services/system'

export const systemKeys = {
  health: ['system', 'health'] as const,
  config: ['system', 'config'] as const,
}

/** Health is polled: a dependency can drop while the tab stays open. */
export function useHealth(refetchInterval = 30_000) {
  return useQuery({
    queryKey: systemKeys.health,
    queryFn: systemApi.health,
    refetchInterval,
    staleTime: 10_000,
    retry: 1,
  })
}

/** Configuration only changes when the backend restarts, so it is fetched once. */
export function useConfig() {
  return useQuery({
    queryKey: systemKeys.config,
    queryFn: systemApi.config,
    staleTime: Number.POSITIVE_INFINITY,
    retry: 1,
  })
}
