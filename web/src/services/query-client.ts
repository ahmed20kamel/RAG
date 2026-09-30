import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './client'

/**
 * Built per mount rather than once per module.
 *
 * The cache is scoped to a signed-in identity, so it must not outlive the component
 * that owns it. A module-level client would also carry one test's answers into the
 * next, which is how a stale identity leaks across a remount.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        // A 404 is an answer, not a transient failure: retrying it only delays the empty
        // state the user should already be looking at.
        retry: (failureCount, error) =>
          error instanceof ApiError && error.status >= 400 && error.status < 500
            ? false
            : failureCount < 2,
      },
    },
  })
}
