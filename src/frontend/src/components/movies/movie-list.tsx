import { ChevronDown } from 'lucide-react'

import { CsfdLink, GenreTags, MovieFacts } from '@/components/movies/movie-meta'
import { MoviePoster } from '@/components/movies/movie-poster'
import { cn } from '@/lib/utils'
import type { Movie } from '@/lib/api'

interface MovieListProps {
  movies: Movie[]
  selectedMovieId: number | null
  onSelectMovie: (movieId: number) => void
}

/**
 * Movie rows. Selecting a row collapses its header to just the title and expands
 * the poster, description and genres below it; the parent uses the selection to light up screening days on the
 * calendar.
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
              {/* Collapsed: thumbnail + one-line summary. Expanded: title only - the
                  poster and details move into the panel below. */}
              {!isSelected && <MoviePoster movie={movie} className="w-10" />}
              <div className="flex-1">
                <div
                  className={cn(
                    'text-sm font-medium',
                    isSelected ? 'text-brand' : 'text-surface-foreground',
                  )}
                >
                  {movie.title}
                </div>
                {!isSelected && (
                  <div className="text-xs text-muted-foreground">
                    <MovieFacts movie={movie} />
                    {movie.genres.length > 0 && ` · ${movie.genres.join(', ')}`}
                  </div>
                )}
              </div>
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
              <div className="overflow-hidden" inert={!isSelected}>
                <div className="flex gap-4 p-4 pt-0">
                  <MoviePoster movie={movie} className="w-32" />
                  <div className="flex-1 mt-2">
                    {/* Metadata first, then a divider, then the description. */}
                    <div className="space-y-3">
                      <GenreTags genres={movie.genres} />
                      <MovieFacts
                        movie={movie}
                        className="block text-sm font-medium text-surface-foreground"
                      />
                    </div>
                    <p className="mt-3 border-t border-border pt-3 text-sm leading-relaxed text-muted-foreground">
                      {movie.description}
                    </p>
                    <div className="mt-3 flex justify-end">
                      <CsfdLink url={movie.csfd_url} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
