import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Skeleton } from '@/components/ui/primitives'
import { useCurrentUser } from '@/hooks/useAuth'

/**
 * Gate in front of every application route.
 *
 * While identity is unknown it renders a placeholder rather than redirecting: sending
 * a signed-in user to the login page for the half-second before `/me` answers would
 * lose whatever they were doing.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: user, isLoading } = useCurrentUser()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="route-fallback">
        <Skeleton width="12rem" height="1.75rem" />
        <Skeleton width="100%" height="9rem" radius="14px" />
      </div>
    )
  }

  if (!user) {
    // Where they were going is kept, so the login lands them there instead of home.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  return <>{children}</>
}
