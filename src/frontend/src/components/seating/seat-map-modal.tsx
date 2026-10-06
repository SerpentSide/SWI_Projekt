import * as React from 'react'
import { X } from 'lucide-react'

import { ApiError, api, type Seat } from '@/lib/api'
import { requestNotificationCheck } from '@/lib/notifications'
import { hasIsolatedFreeSeat } from '@/lib/seating'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth-provider'

interface SeatMapModalProps {
  title: string
  subtitle: string
  screeningId: number
  onClose: () => void
}

/** Groups the hall's seats into rows (by row label), each sorted left-to-right. */
function groupRows(seats: Seat[]): [string, Seat[]][] {
  const rows = new Map<string, Seat[]>()
  for (const seat of seats) {
    const row = rows.get(seat.row_label) ?? []
    row.push(seat)
    rows.set(seat.row_label, row)
  }
  return Array.from(rows.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([label, row]) => [label, row.sort((a, b) => a.seat_number - b.seat_number)])
}

export function SeatMapModal({ title, subtitle, screeningId, onClose }: SeatMapModalProps) {
  const { user } = useAuth()
  const [seats, setSeats] = React.useState<Seat[] | null>(null)
  const [selectedSeats, setSelectedSeats] = React.useState<Set<number>>(new Set())
  const [isSubmitting, setIsSubmitting] = React.useState(false)
  const [serverError, setServerError] = React.useState<string | null>(null)
  const [reserved, setReserved] = React.useState(false)
  const [awaitingApproval, setAwaitingApproval] = React.useState(false)

  const loadSeats = React.useCallback(() => {
    api
      .availability(screeningId)
      .then((data) => setSeats(data.seats))
      .catch(() => setServerError('Nepodařilo se načíst sedadla.'))
  }, [screeningId])

  React.useEffect(loadSeats, [loadSeats])

  React.useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const rows = React.useMemo(() => groupRows(seats ?? []), [seats])
  const seatNumbers = rows[0]?.[1].map((s) => s.seat_number) ?? []
  const gridStyle = { gridTemplateColumns: `1.5rem repeat(${seatNumbers.length}, 1.75rem)` }

  // Checked against the whole selection rather than blocking each click - two
  // seats that each look "isolated" on their own can be picked together fine
  // (e.g. both sides of a gap), so this can only be judged once both are picked.
  const invalidRows = React.useMemo(
    () =>
      rows
        .filter(([, row]) =>
          hasIsolatedFreeSeat(row.map((s) => s.occupied || selectedSeats.has(s.id))),
        )
        .map(([label]) => label),
    [rows, selectedSeats],
  )

  const canReserve =
    !reserved && !isSubmitting && selectedSeats.size > 0 && invalidRows.length === 0

  function handleToggleSeat(seat: Seat) {
    if (seat.occupied || reserved) return
    setServerError(null)
    setSelectedSeats((current) => {
      const next = new Set(current)
      if (next.has(seat.id)) {
        next.delete(seat.id)
      } else {
        next.add(seat.id)
      }
      return next
    })
  }

  async function handleReserve() {
    if (!canReserve || !user) return
    setIsSubmitting(true)
    setServerError(null)
    try {
      // No payment step yet, so the hold is confirmed straight away.
      const { reservation_id } = await api.createReservation(
        user.user_id,
        screeningId,
        Array.from(selectedSeats),
      )
      const { state } = await api.confirmReservation(reservation_id)
      setAwaitingApproval(state === 'PENDING_APPROVAL')
      setReserved(true)
      requestNotificationCheck() // show the "confirmed" / "saved" notification right away
    } catch (err) {
      setServerError(err instanceof ApiError ? err.message : 'Rezervace se nezdařila.')
      setSelectedSeats(new Set())
    } finally {
      setIsSubmitting(false)
      loadSeats()
    }
  }

  const message = serverError
    ? serverError
    : reserved
      ? awaitingApproval
        ? 'Rezervace čeká na schválení pokladnou - sedadla jsou zatím držena.'
        : `Rezervace potvrzena (${selectedSeats.size} ${selectedSeats.size === 1 ? 'sedadlo' : 'sedadla'}).`
      : invalidRows.length > 0
        ? `Tento výběr by nechal osamocené volné sedadlo v řadě ${invalidRows.join(', ')} - uprav výběr, než budeš moct rezervovat.`
        : null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] overflow-y-auto rounded-DEFAULT border border-border bg-surface p-6"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">{title}</h2>
            <p className="text-sm capitalize text-muted-foreground">{subtitle}</p>
          </div>
          <button
            type="button"
            aria-label="Zavřít"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="mb-4 flex flex-wrap gap-4 text-xs text-muted-foreground">
          <LegendItem colorClassName="bg-muted" label="Volné" />
          <LegendItem colorClassName="bg-destructive" label="Zabrané" />
          <LegendItem colorClassName="bg-brand" label="Vybrané" />
        </div>

        {seats === null ? (
          <p className="text-sm text-muted-foreground">Načítání…</p>
        ) : (
          <div className="inline-block">
            <div className="mb-1 grid gap-1" style={gridStyle}>
              <div />
              {seatNumbers.map((number) => (
                <div
                  key={number}
                  className="flex h-6 items-center justify-center text-xs font-medium text-muted-foreground"
                >
                  {number}
                </div>
              ))}
            </div>

            {rows.map(([label, row]) => (
              <div key={label} className="mb-1 grid gap-1" style={gridStyle}>
                <div className="flex h-7 items-center justify-center text-xs font-medium text-muted-foreground">
                  {label}
                </div>
                {row.map((seat) => {
                  const isSelected = selectedSeats.has(seat.id)
                  const name = `${seat.row_label}${seat.seat_number}`
                  return (
                    <button
                      key={seat.id}
                      type="button"
                      disabled={seat.occupied}
                      aria-label={`Sedadlo ${name}${seat.occupied ? ' (zabrané)' : ''}`}
                      onClick={() => handleToggleSeat(seat)}
                      className={cn(
                        'h-7 w-7 rounded-DEFAULT text-[10px] font-medium transition-colors',
                        seat.occupied &&
                          'cursor-not-allowed bg-destructive text-destructive-foreground',
                        !seat.occupied &&
                          isSelected &&
                          'bg-brand text-brand-foreground hover:bg-brand-dark',
                        !seat.occupied &&
                          !isSelected &&
                          'bg-muted text-muted-foreground hover:bg-muted-foreground/30',
                      )}
                    />
                  )
                })}
              </div>
            ))}
          </div>
        )}

        {/* Always mounted at a fixed height, just hidden when there's nothing to say -
            otherwise the message appearing/disappearing resizes the modal and it
            jumps around (it's centered on screen). */}
        <p
          className={cn(
            'mt-3 min-h-10 max-w-80 text-sm',
            reserved && !serverError ? 'text-brand' : 'text-destructive',
            !message && 'invisible',
          )}
        >
          {message}
        </p>

        {reserved ? (
          <button
            type="button"
            onClick={onClose}
            className="mt-4 w-full rounded-DEFAULT bg-brand py-2 text-sm font-medium text-brand-foreground hover:bg-brand-dark"
          >
            Hotovo
          </button>
        ) : (
          <button
            type="button"
            disabled={!canReserve}
            onClick={handleReserve}
            className={cn(
              'mt-4 w-full rounded-DEFAULT py-2 text-sm font-medium transition-colors',
              canReserve
                ? 'cursor-pointer bg-brand text-brand-foreground hover:bg-brand-dark'
                : 'cursor-not-allowed bg-muted text-muted-foreground',
            )}
          >
            {isSubmitting
              ? 'Rezervuji…'
              : `Rezervovat${selectedSeats.size > 0 ? ` (${selectedSeats.size})` : ''}`}
          </button>
        )}
      </div>
    </div>
  )
}

function LegendItem({ colorClassName, label }: { colorClassName: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={cn('h-3 w-3 rounded-sm', colorClassName)} />
      {label}
    </div>
  )
}
