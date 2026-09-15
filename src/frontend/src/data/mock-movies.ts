export interface MockMovie {
  id: string
  title: string
  /** Monday-first weekday indices (0 = Po ... 6 = Ne) this placeholder screens on. */
  playsOnWeekdays: number[]
}

// Placeholders only - real screenings will come from the backend once it exists.
export const MOCK_MOVIES: MockMovie[] = [
  { id: 'film-1', title: 'Film 1', playsOnWeekdays: [0, 2, 4] },
  { id: 'film-2', title: 'Film 2', playsOnWeekdays: [1, 3] },
  { id: 'film-3', title: 'Film 3', playsOnWeekdays: [5, 6] },
  { id: 'film-4', title: 'Film 4', playsOnWeekdays: [0, 1, 2, 3, 4] },
]
