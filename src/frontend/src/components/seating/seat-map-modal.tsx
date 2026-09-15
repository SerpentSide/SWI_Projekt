import * as React from 'react'
import { X } from 'lucide-react'

import {
  COLUMN_LETTERS,
  SEAT_COLS,
  SEAT_ROWS,
  generateMockTakenSeats,
  hasIsolatedFreeSeat,
  seatId,
} from '@/lib/seating'
import { cn } from '@/lib/utils'

interface SeatMapModalProps {
  title: string
  subtitle: string
  /** Unique per screening (e.g. movie id + date + time) - seeds the mock seat layout. */
  screeningSeed: string
  onClose: () => void
}

export function SeatMapModal({ title, subtitle, screeningSeed, onClose }: SeatMapModalProps) {
  const takenSeats = React.useMemo(() => generateMockTakenSeats(screeningSeed), [screeningSeed])
  const [selectedSeats, setSelectedSeats] = React.useState<Set<string>>(new Set())
  const [warning, setWarning] = React.useState<string | null>(null)

  React.useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  function handleToggleSeat(row: number, col: number) {
    const id = seatId(row, col)
    if (takenSeats.has(id)) return

    setSelectedSeats((current) => {
      const next = new Set(current)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }

      const rowOccupied = Array.from(
        { length: SEAT_COLS },
        (_, c) => takenSeats.has(seatId(row, c)) || next.has(seatId(row, c)),
      )
      if (hasIsolatedFreeSeat(rowOccupied)) {
        setWarning('Tímto by zůstalo osamocené volné sedadlo - to není dovoleno.')
        return current
      }

      setWarning(null)
      return next
    })
  }

  function handleReserve() {
    if (selectedSeats.size === 0) return
    // TODO: replace with a real POST /reservations once the backend exists.
    onClose()
  }

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

        <div className="inline-block">
          <div className="mb-1 grid grid-cols-[1.5rem_repeat(10,1.75rem)] gap-1">
            <div />
            {COLUMN_LETTERS.map((letter) => (
              <div
                key={letter}
                className="flex h-6 items-center justify-center text-xs font-medium uppercase text-muted-foreground"
              >
                {letter}
              </div>
            ))}
          </div>

          {Array.from({ length: SEAT_ROWS }, (_, rowIndex) => {
            const row = rowIndex + 1
            return (
              <div key={row} className="mb-1 grid grid-cols-[1.5rem_repeat(10,1.75rem)] gap-1">
                <div className="flex h-7 items-center justify-center text-xs font-medium text-muted-foreground">
                  {row}
                </div>
                {Array.from({ length: SEAT_COLS }, (_, col) => {
                  const id = seatId(row, col)
                  const isTaken = takenSeats.has(id)
                  const isSelected = selectedSeats.has(id)

                  return (
                    <button
                      key={id}
                      type="button"
                      disabled={isTaken}
                      aria-label={`Sedadlo ${id}${isTaken ? ' (zabrané)' : ''}`}
                      onClick={() => handleToggleSeat(row, col)}
                      className={cn(
                        'h-7 w-7 rounded-DEFAULT text-[10px] font-medium transition-colors',
                        isTaken && 'cursor-not-allowed bg-destructive text-destructive-foreground',
                        !isTaken &&
                          isSelected &&
                          'bg-brand text-brand-foreground hover:bg-brand-dark',
                        !isTaken &&
                          !isSelected &&
                          'bg-muted text-muted-foreground hover:bg-muted-foreground/30',
                      )}
                    />
                  )
                })}
              </div>
            )
          })}
        </div>

        {warning && <p className="mt-3 text-sm text-destructive">{warning}</p>}

        <button
          type="button"
          disabled={selectedSeats.size === 0}
          onClick={handleReserve}
          className={cn(
            'mt-4 w-full rounded-DEFAULT py-2 text-sm font-medium transition-colors',
            selectedSeats.size === 0
              ? 'cursor-not-allowed bg-muted text-muted-foreground'
              : 'cursor-pointer bg-brand text-brand-foreground hover:bg-brand-dark',
          )}
        >
          Rezervovat{selectedSeats.size > 0 ? ` (${selectedSeats.size})` : ''}
        </button>
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
