import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'
import type { MockMovie } from '@/data/mock-movies'

interface MovieListProps {
  movies: MockMovie[]
  selectedMovieId: string | null
  onSelectMovie: (movieId: string) => void
}

/**
 * Placeholder movie rows. Selecting a row expands it to show a bigger poster
 * placeholder; the parent uses the selection to light up screening days on
 * the calendar above.
 */
export function MovieList({ movies, selectedMovieId, onSelectMovie }: MovieListProps) {
  return (
    <div className="divide-y divide-border rounded-DEFAULT border border-border bg-surface">
      {movies.map((movie) => {
        const isSelected = movie.id === selectedMovieId
        return (
          <div key={movie.id}>
            <button
              type="button"
              onClick={() => onSelectMovie(movie.id)}
              aria-expanded={isSelected}
              className={cn(
                'flex w-full items-center gap-3 p-3 text-left transition-colors',
                isSelected ? 'bg-brand/10' : 'hover:bg-muted',
              )}
            >
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-DEFAULT border border-dashed border-muted-foreground/40 text-[10px] text-muted-foreground">
                plakát
              </div>
              <span
                className={cn(
                  'flex-1 text-sm font-medium',
                  isSelected ? 'text-brand' : 'text-surface-foreground',
                )}
              >
                {movie.title}
              </span>
              <ChevronDown
                className={cn(
                  'h-4 w-4 shrink-0 text-muted-foreground transition-transform',
                  isSelected && 'rotate-180',
                )}
              />
            </button>

            {/* Grid-rows trick animates a height that would otherwise be "auto". */}
            <div
              className={cn(
                'grid transition-[grid-template-rows] duration-300 ease-out',
                isSelected ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
              )}
            >
              <div className="overflow-hidden">
                <div className="flex gap-4 p-4 pt-0">
                  <div className="flex h-48 w-32 shrink-0 items-center justify-center rounded-DEFAULT border border-dashed border-muted-foreground/40 text-xs text-muted-foreground">
                    plakát
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Zatím placeholder — popis filmu, délka a další detaily přijdou, až
                    bude API.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
