import { ArrowLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { MockMovie } from '@/data/mock-movies'

const DATE_FORMATTER = new Intl.DateTimeFormat('cs-CZ', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
})

interface MovieDetailProps {
  movie: MockMovie
  date: Date
  onBack: () => void
}

/** Shown in place of the movie list once a screening day has been picked on the calendar. */
export function MovieDetail({ movie, date, onBack }: MovieDetailProps) {
  return (
    <div className="p-1">
      <Button variant="ghost" size="sm" onClick={onBack} className="mb-4">
        <ArrowLeft className="h-4 w-4" />
        Zpět na seznam
      </Button>

      <h2 className="text-xl font-semibold">{movie.title}</h2>
      <p className="mb-4 text-sm capitalize text-muted-foreground">
        {DATE_FORMATTER.format(date)}
      </p>

      <div className="flex flex-wrap gap-2">
        {movie.screeningTimes.map((time) => (
          <button
            key={time}
            type="button"
            className="rounded-DEFAULT border border-brand px-4 py-2 text-sm font-medium text-brand transition-colors hover:bg-brand hover:text-brand-foreground"
          >
            {time}
          </button>
        ))}
      </div>
    </div>
  )
}
