const CHECK_EVENT = 'cinema:check-notifications'

/**
 * Ask the notification poller (NotificationToggle in the header) to check right now
 * instead of waiting for its next tick - e.g. right after a reservation is confirmed.
 */
export function requestNotificationCheck() {
  window.dispatchEvent(new Event(CHECK_EVENT))
}

export function onNotificationCheckRequested(handler: () => void): () => void {
  window.addEventListener(CHECK_EVENT, handler)
  return () => window.removeEventListener(CHECK_EVENT, handler)
}
