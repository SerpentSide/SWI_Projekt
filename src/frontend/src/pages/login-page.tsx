import * as React from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/theme-toggle'
import { useAuth } from '@/providers/auth-provider'

interface LocationState {
  from?: { pathname: string }
}

export function LoginPage() {
  const { isAuthenticated, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [isSubmitting, setIsSubmitting] = React.useState(false)

  if (isAuthenticated) {
    const from = (location.state as LocationState | null)?.from?.pathname ?? '/'
    return <Navigate to={from} replace />
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setIsSubmitting(true)
    try {
      await login(email, password)
      const from = (location.state as LocationState | null)?.from?.pathname ?? '/'
      navigate(from, { replace: true })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6 text-foreground">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>

      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-DEFAULT border border-border bg-surface p-6"
      >
        <div className="space-y-1 text-center">
          <h1 className="text-xl font-semibold text-brand">Cinema Reservations</h1>
          <p className="text-sm text-muted-foreground">Přihlaš se ke svému účtu</p>
        </div>

        <div className="space-y-2">
          <label htmlFor="email" className="text-sm font-medium">
            E-mail
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-DEFAULT border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-brand"
            placeholder="jan@example.com"
          />
        </div>

        <div className="space-y-2">
          <label htmlFor="password" className="text-sm font-medium">
            Heslo
          </label>
          <input
            id="password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-DEFAULT border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-brand"
            placeholder="••••••••"
          />
        </div>

        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? 'Přihlašování…' : 'Přihlásit se'}
        </Button>

        <p className="text-center text-xs text-muted-foreground">
          Backend ještě neexistuje — jakékoli přihlašovací údaje tě zatím přihlásí.
        </p>
      </form>
    </div>
  )
}
