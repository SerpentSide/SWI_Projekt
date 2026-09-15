import * as React from 'react'

import { MonthCalendar } from '@/components/calendar/month-calendar'
import { MovieList } from '@/components/movies/movie-list'
import { MOCK_MOVIES } from '@/data/mock-movies'
import { getMonthWeeks } from '@/lib/calendar'

export function HomePage() {
  const today = new Date()
  const [year, setYear] = React.useState(today.getFullYear())
  const [month, setMonth] = React.useState(today.getMonth())
  const [selectedMovieId, setSelectedMovieId] = React.useState<string | null>(null)

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

  function handleSelectMovie(movieId: string) {
    setSelectedMovieId((current) => (current === movieId ? null : movieId))
  }

  const highlightedDays = React.useMemo(() => {
    const movie = MOCK_MOVIES.find((m) => m.id === selectedMovieId)
    if (!movie) return undefined

    const weekdaySet = new Set(movie.playsOnWeekdays)
    const days = new Set<number>()
    for (const week of getMonthWeeks(year, month)) {
      for (const cell of week) {
        if (cell && weekdaySet.has((cell.date.getDay() + 6) % 7)) {
          days.add(cell.day)
        }
      }
    }
    return days
  }, [selectedMovieId, year, month])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Přehled promítání</h1>
        <p className="text-muted-foreground">
          Zatím placeholder — dny promítání se dopočítávají z ukázkových filmů, až bude
          API, nahradí se reálnými promítáními.
        </p>
      </div>

      <MonthCalendar
        year={year}
        month={month}
        onPrevMonth={goToPrevMonth}
        onNextMonth={goToNextMonth}
        highlightedDays={highlightedDays}
      />

      <MovieList
        movies={MOCK_MOVIES}
        selectedMovieId={selectedMovieId}
        onSelectMovie={handleSelectMovie}
      />
    </div>
  )
}
