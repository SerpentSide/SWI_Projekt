import { Outlet } from 'react-router-dom'

import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/providers/auth-provider'

/**
 * Shared chrome for every authenticated screen: header with branding, theme
 * toggle and logout, then the routed page content below.
 */
export function AppShell() {
  const { logout } = useAuth()

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <span className="text-lg font-semibold text-brand">Cinema Reservations</span>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button variant="ghost" size="sm" onClick={logout}>
            Odhlásit
          </Button>
        </div>
      </header>

      <main className="p-6">
        <Outlet />
      </main>
    </div>
  )
}
