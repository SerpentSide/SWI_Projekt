import * as React from 'react'

import { MonthCalendar } from '@/components/calendar/month-calendar'
import { MovieDetail } from '@/components/movies/movie-detail'
import { MovieList } from '@/components/movies/movie-list'
import { MOCK_MOVIES } from '@/data/mock-movies'
import { getMonthWeeks } from '@/lib/calendar'

export function HomePage() {
  const today = new Date()
  const [year, setYear] = React.useState(today.getFullYear())
  const [month, setMonth] = React.useState(today.getMonth())
  const [selectedMovieId, setSelectedMovieId] = React.useState<string | null>(null)
  const [selectedDate, setSelectedDate] = React.useState<Date | null>(null)

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
    setSelectedDate(null)
    setSelectedMovieId((current) => (current === movieId ? null : movieId))
  }

  const selectedMovie = MOCK_MOVIES.find((m) => m.id === selectedMovieId) ?? null

  const highlightedDays = React.useMemo(() => {
    if (!selectedMovie) return undefined

    const weekdaySet = new Set(selectedMovie.playsOnWeekdays)
    const days = new Set<number>()
    for (const week of getMonthWeeks(year, month)) {
      for (const cell of week) {
        if (cell && weekdaySet.has((cell.date.getDay() + 6) % 7)) {
          days.add(cell.day)
        }
      }
    }
    return days
  }, [selectedMovie, year, month])

  return (
    <div className="flex h-full gap-6">
      {/* Fixed left column: calendar, then a featured poster below it. Neither
          scrolls or moves - only the list on the right does. */}
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
        {/* TODO: rotate through featured posters on an interval once there's real data. */}
        <div className="flex aspect-[2/3] items-center justify-center rounded-DEFAULT border border-dashed border-muted-foreground/40 text-sm text-muted-foreground">
          plakát
        </div>
      </div>

      {/* Full height, scrollable, scrollbar hidden. */}
      <div className="scrollbar-hide flex-1 overflow-y-auto">
        {selectedMovie && selectedDate ? (
          <MovieDetail
            movie={selectedMovie}
            date={selectedDate}
            onBack={() => setSelectedDate(null)}
          />
        ) : (
          <MovieList
            movies={MOCK_MOVIES}
            selectedMovieId={selectedMovieId}
            onSelectMovie={handleSelectMovie}
          />
        )}
      </div>
    </div>
  )
}
