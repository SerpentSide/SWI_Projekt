import { cn } from '@/lib/utils'
import type { MockMovie } from '@/data/mock-movies'

interface MovieListProps {
  movies: MockMovie[]
  selectedMovieId: string | null
  onSelectMovie: (movieId: string) => void
}

/**
 * Placeholder movie cards. Clicking one toggles its selection - the parent uses
 * that to light up the days it screens on in the calendar above.
 */
export function MovieList({ movies, selectedMovieId, onSelectMovie }: MovieListProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
      {movies.map((movie) => {
        const isSelected = movie.id === selectedMovieId
        return (
          <button
            key={movie.id}
            type="button"
            onClick={() => onSelectMovie(movie.id)}
            aria-pressed={isSelected}
            className={cn(
              'flex aspect-[2/3] flex-col items-center justify-center gap-2 rounded-DEFAULT border p-3 text-center transition-colors',
              isSelected
                ? 'border-brand bg-brand/10 text-brand'
                : 'border-border bg-surface text-surface-foreground hover:border-brand',
            )}
          >
            <div className="flex h-full w-full items-center justify-center rounded-DEFAULT border border-dashed border-muted-foreground/40 text-xs text-muted-foreground">
              plakát
            </div>
            <span className="text-sm font-medium">{movie.title}</span>
          </button>
        )
      })}
    </div>
  )
}
