import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authApi } from '@/services/auth'
import { ApiError } from '@/services/client'
import type { CurrentUser, Permission } from '@/types/auth'

export const authKeys = { me: ['auth', 'me'] as const }

/**
 * Who is signed in.
 *
 * A 401 is a definitive answer — nobody is — not a transient failure, so it resolves to
 * `null` rather than an error state the whole interface would have to handle.
 */
export function useCurrentUser() {
  return useQuery<CurrentUser | null>({
    queryKey: authKeys.me,
    queryFn: async () => {
      try {
        return await authApi.me()
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
    staleTime: 60_000,
    retry: false,
  })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: authApi.login,
    onSuccess: (user) => {
      client.setQueryData(authKeys.me, user)
      // Everything cached was fetched as whoever was here before — possibly nobody.
      void client.invalidateQueries()
    },
  })
}

export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: authApi.logout,
    onSettled: () => {
      client.setQueryData(authKeys.me, null)
      client.clear()
    },
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: ({ current, next }: { current: string; next: string }) =>
      authApi.changePassword(current, next),
  })
}

/**
 * Capability check. The server enforces this too; here it only decides what to show.
 *
 * Every step is optional on purpose. This runs on the first paint of every page, so a
 * response that is missing `permissions` — an older backend, a proxy error page parsed
 * as JSON — must deny the capability, not take the whole interface down with it.
 */
export function can(user: CurrentUser | null | undefined, permission: Permission): boolean {
  return Boolean(user?.permissions?.includes(permission))
}
