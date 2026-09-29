import * as React from 'react'
import { ArrowLeft } from 'lucide-react'

import { CsfdLink, GenreTags, MovieFacts } from '@/components/movies/movie-meta'
import { MoviePoster } from '@/components/movies/movie-poster'
import { Button } from '@/components/ui/button'
import { SeatMapModal } from '@/components/seating/seat-map-modal'
import type { Movie, Screening } from '@/lib/api'
import { cn } from '@/lib/utils'

const DATE_FORMATTER = new Intl.DateTimeFormat('cs-CZ', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
})

const TIME_FORMATTER = new Intl.DateTimeFormat('cs-CZ', { hour: '2-digit', minute: '2-digit' })

interface MovieDetailProps {
  movie: Movie
  date: Date
  /** This movie's screenings on `date`. */
  screenings: Screening[]
  onBack: () => void
}

/** Shown in place of the movie list once a screening day has been picked on the calendar. */
export function MovieDetail({ movie, date, screenings, onBack }: MovieDetailProps) {
  const [selected, setSelected] = React.useState<Screening | null>(null)
  const dateLabel = DATE_FORMATTER.format(date)
  const [now] = React.useState(() => Date.now())

  return (
    <div className="p-1">
      <Button variant="ghost" size="sm" onClick={onBack} className="mb-4">
        <ArrowLeft className="h-4 w-4" />
        Zpět na seznam
      </Button>

      <div className="mb-6 flex gap-4">
        <MoviePoster movie={movie} className="w-28" />
        <div className="space-y-2">
          <h2 className="text-xl font-semibold">{movie.title}</h2>
          <GenreTags genres={movie.genres} />
          <MovieFacts movie={movie} className="block text-sm text-muted-foreground" />
          <p className="mt-3 max-w-prose border-t border-border pt-3 text-sm text-muted-foreground">
            {movie.description}
          </p>
          <div className="mt-3 flex justify-end">
            <CsfdLink url={movie.csfd_url} />
          </div>
        </div>
      </div>

      <p className="mb-2 text-sm font-medium capitalize">{dateLabel}</p>
      <div className="flex flex-wrap gap-2">
        {screenings.map((screening) => {
          const isPast = new Date(screening.starts_at).getTime() <= now
          return (
            <button
              key={screening.id}
              type="button"
              disabled={isPast}
              onClick={() => setSelected(screening)}
              title={screening.hall_name}
              className={cn(
                'rounded-DEFAULT border px-4 py-2 text-sm font-medium transition-colors',
                isPast
                  ? 'cursor-not-allowed border-border text-muted-foreground'
                  : 'border-brand text-brand hover:bg-brand hover:text-brand-foreground',
              )}
            >
              {TIME_FORMATTER.format(new Date(screening.starts_at))}
            </button>
          )
        })}
      </div>

      {selected && (
        <SeatMapModal
          title={movie.title}
          subtitle={`${dateLabel}, ${TIME_FORMATTER.format(new Date(selected.starts_at))} · ${selected.hall_name}`}
          screeningId={selected.id}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}
