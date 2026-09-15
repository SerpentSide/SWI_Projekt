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
    <div className="flex h-screen flex-col bg-background text-foreground">
      <header className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
        <span className="text-lg font-semibold text-brand">Cinema Reservations</span>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <Button variant="ghost" size="sm" onClick={logout}>
            Odhlásit
          </Button>
        </div>
      </header>

      {/* Only this area scrolls - the header above stays put. */}
      <main className="flex-1 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}
