import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '@/providers/auth-provider'

/**
 * Gate for every route that requires a logged-in user. Unauthenticated visitors
 * are bounced to /login, remembering where they were headed so login can send
 * them back.
 */
export function ProtectedRoute() {
  const { isAuthenticated } = useAuth()
  const location = useLocation()

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <Outlet />
}
