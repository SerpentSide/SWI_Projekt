import * as React from 'react'
import { Bell, BellOff, BellRing } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { onNotificationCheckRequested } from '@/lib/notifications'
import { useAuth } from '@/providers/auth-provider'

const POLL_INTERVAL_MS = 15_000
const isSupported = typeof window !== 'undefined' && 'Notification' in window

/**
 * Header bell: asks for browser-notification permission, and while it's granted polls
 * the backend for the user's undelivered notifications and shows each one as a browser
 * notification. Without permission nothing is fetched, so messages wait on the server
 * until notifications are turned on.
 */
export function NotificationToggle() {
  const { user } = useAuth()
  const [permission, setPermission] = React.useState<NotificationPermission>(() =>
    isSupported ? Notification.permission : 'denied',
  )

  React.useEffect(() => {
    if (!user || permission !== 'granted') return
    let busy = false

    async function check() {
      if (busy || !user) return
      busy = true
      try {
        for (const n of await api.deliverNotifications(user.user_id)) {
          new Notification(n.title, { body: n.body, tag: `cinema-${n.id}`, icon: '/favicon.svg' })
        }
      } catch {
        // Backend unreachable - try again on the next tick.
      } finally {
        busy = false
      }
    }

    void check()
    const interval = window.setInterval(check, POLL_INTERVAL_MS)
    const unsubscribe = onNotificationCheckRequested(() => void check())
    return () => {
      window.clearInterval(interval)
      unsubscribe()
    }
  }, [user, permission])

  if (!isSupported) return null

  if (permission === 'granted') {
    return (
      <Button variant="ghost" size="icon" aria-label="Oznámení jsou zapnutá" title="Oznámení jsou zapnutá">
        <BellRing className="h-5 w-5" />
      </Button>
    )
  }

  if (permission === 'denied') {
    return (
      <Button
        variant="ghost"
        size="icon"
        aria-label="Oznámení jsou v prohlížeči zablokovaná"
        title="Oznámení jsou v prohlížeči zablokovaná - povol je v nastavení stránky"
      >
        <BellOff className="h-5 w-5 text-muted-foreground" />
      </Button>
    )
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Zapnout oznámení"
      title="Zapnout oznámení"
      onClick={() => void Notification.requestPermission().then(setPermission)}
    >
      <Bell className="h-5 w-5" />
    </Button>
  )
}
