import { ChevronLeft, ChevronRight } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { MONTH_NAMES, WEEKDAY_LABELS, getMonthWeeks, isSameDay } from '@/lib/calendar'
import { cn } from '@/lib/utils'

interface MonthCalendarProps {
  year: number
  month: number
  onPrevMonth: () => void
  onNextMonth: () => void
  /** Days (1-31) in the displayed month that should be highlighted. */
  highlightedDays?: Set<number>
}

export function MonthCalendar({
  year,
  month,
  onPrevMonth,
  onNextMonth,
  highlightedDays,
}: MonthCalendarProps) {
  const weeks = getMonthWeeks(year, month)
  const today = new Date()

  return (
    <div className="rounded-DEFAULT border border-border bg-surface p-4">
      <div className="mb-4 flex items-center justify-between">
        <Button variant="ghost" size="icon" aria-label="Předchozí měsíc" onClick={onPrevMonth}>
          <ChevronLeft className="h-5 w-5" />
        </Button>
        <span className="text-lg font-semibold">
          {MONTH_NAMES[month]} {year}
        </span>
        <Button variant="ghost" size="icon" aria-label="Další měsíc" onClick={onNextMonth}>
          <ChevronRight className="h-5 w-5" />
        </Button>
      </div>

      <div className="grid grid-cols-7 gap-1 text-center text-xs font-medium text-muted-foreground">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="py-1">
            {label}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-1">
        {weeks.flatMap((week, weekIndex) =>
          week.map((cell, dayIndex) => {
            if (!cell) {
              return <div key={`${weekIndex}-${dayIndex}`} className="aspect-square" />
            }

            const isToday = isSameDay(cell.date, today)
            const isHighlighted = highlightedDays?.has(cell.day) ?? false

            return (
              <div
                key={`${weekIndex}-${dayIndex}`}
                className={cn(
                  'flex aspect-square items-center justify-center rounded-DEFAULT text-sm transition-colors',
                  isHighlighted
                    ? 'bg-brand text-brand-foreground font-semibold'
                    : 'text-foreground',
                  isToday && !isHighlighted && 'ring-1 ring-inset ring-brand',
                )}
              >
                {cell.day}
              </div>
            )
          }),
        )}
      </div>
    </div>
  )
}
