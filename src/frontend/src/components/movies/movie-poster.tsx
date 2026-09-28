import { cn } from '@/lib/utils'
import type { Movie } from '@/lib/api'

interface MoviePosterProps {
  movie: Movie | null
  className?: string
}

/** 2:3 poster, or the dashed placeholder when there's no movie / no poster URL. */
export function MoviePoster({ movie, className }: MoviePosterProps) {
  if (movie?.poster_url) {
    return (
      <img
        src={movie.poster_url}
        alt={`Plakát: ${movie.title}`}
        // Posters are hotlinked from ČSFD's image CDN; don't leak our origin to it.
        referrerPolicy="no-referrer"
        loading="lazy"
        className={cn('aspect-[2/3] shrink-0 rounded-DEFAULT object-cover', className)}
      />
    )
  }
  return (
    <div
      className={cn(
        'flex aspect-[2/3] shrink-0 items-center justify-center rounded-DEFAULT border border-dashed border-muted-foreground/40 text-xs text-muted-foreground',
        className,
      )}
    >
      plakát
    </div>
  )
}
