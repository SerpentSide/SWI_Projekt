import * as React from 'react'

import { MonthCalendar } from '@/components/calendar/month-calendar'
import { MovieDetail } from '@/components/movies/movie-detail'
import { MovieList } from '@/components/movies/movie-list'
import { api, type Movie, type Screening } from '@/lib/api'
import { isSameDay } from '@/lib/calendar'

export function HomePage() {
  const today = new Date()
  const [year, setYear] = React.useState(today.getFullYear())
  const [month, setMonth] = React.useState(today.getMonth())
  const [selectedMovieId, setSelectedMovieId] = React.useState<number | null>(null)
  const [selectedDate, setSelectedDate] = React.useState<Date | null>(null)

  const [movies, setMovies] = React.useState<Movie[]>([])
  const [screenings, setScreenings] = React.useState<Screening[]>([])
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    Promise.all([api.movies(), api.screenings()])
      .then(([movies, screenings]) => {
        setMovies(movies)
        setScreenings(screenings)
      })
      .catch(() => setError('Nepodařilo se načíst filmy - běží backend?'))
  }, [])

  function goToPrevMonth() {
    if (month === 0) {
      setMonth(11)
      setYear((y) => y - 1)
    } else {
      setMonth((m) => m - 1)
    }
  }

  function goToNextMonth() {
    if (month === 11) {
      setMonth(0)
      setYear((y) => y + 1)
    } else {
      setMonth((m) => m + 1)
    }
  }

  function handleSelectMovie(movieId: number) {
    setSelectedDate(null)
    setSelectedMovieId((current) => (current === movieId ? null : movieId))
  }

  const selectedMovie = movies.find((m) => m.id === selectedMovieId) ?? null

  const movieScreenings = React.useMemo(
    () => screenings.filter((s) => s.movie_id === selectedMovieId),
    [screenings, selectedMovieId],
  )

  const highlightedDays = React.useMemo(() => {
    if (!selectedMovie) return undefined

    const days = new Set<number>()
    for (const screening of movieScreenings) {
      const start = new Date(screening.starts_at)
      if (start.getFullYear() === year && start.getMonth() === month) {
        days.add(start.getDate())
      }
    }
    return days
  }, [selectedMovie, movieScreenings, year, month])

  return (
    <div className="flex h-full gap-6">
      {/* Fixed left column with the calendar. It doesn't scroll or move - only the
          list on the right does. */}
      <div className="flex w-64 shrink-0 flex-col gap-4">
        <MonthCalendar
          year={year}
          month={month}
          onPrevMonth={goToPrevMonth}
          onNextMonth={goToNextMonth}
          highlightedDays={highlightedDays}
          selectedDate={selectedDate}
          onDayClick={setSelectedDate}
        />
        {selectedMovie && !selectedDate && (
          <p className="text-sm text-muted-foreground">
            Vyber zvýrazněný den v kalendáři pro výběr promítání.
          </p>
        )}
      </div>

      {/* Full height, scrollable, scrollbar hidden. */}
      <div className="scrollbar-hide flex-1 overflow-y-auto">
        {error ? (
          <p className="p-3 text-sm text-destructive">{error}</p>
        ) : selectedMovie && selectedDate ? (
          <MovieDetail
            movie={selectedMovie}
            date={selectedDate}
            screenings={movieScreenings.filter((s) =>
              isSameDay(new Date(s.starts_at), selectedDate),
            )}
            onBack={() => setSelectedDate(null)}
          />
        ) : (
          <MovieList
            movies={movies}
            selectedMovieId={selectedMovieId}
            onSelectMovie={handleSelectMovie}
          />
        )}
      </div>
    </div>
  )
}
