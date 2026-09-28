import { ExternalLink } from 'lucide-react'

import type { Movie } from '@/lib/api'

/** "1994 · 154 min" */
export function MovieFacts({ movie, className }: { movie: Movie; className?: string }) {
  return (
    <span className={className}>
      {[movie.year, `${movie.duration_minutes} min`].filter(Boolean).join(' · ')}
    </span>
  )
}

export function GenreTags({ genres }: { genres: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {genres.map((genre) => (
        <span
          key={genre}
          className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground"
        >
          {genre}
        </span>
      ))}
    </div>
  )
}

export function CsfdLink({ url }: { url: string | null }) {
  if (!url) return null
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => event.stopPropagation()}
      className="inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline"
    >
      Zobrazit na ČSFD
      <ExternalLink className="h-3.5 w-3.5" />
    </a>
  )
}
