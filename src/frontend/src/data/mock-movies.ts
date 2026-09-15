export interface MockMovie {
  id: string
  title: string
  /** Monday-first weekday indices (0 = Po ... 6 = Ne) this placeholder screens on. */
  playsOnWeekdays: number[]
  /** Same showtimes on every day it screens - just enough to demo the detail view. */
  screeningTimes: string[]
}

// Placeholders only - real screenings will come from the backend once it exists.
export const MOCK_MOVIES: MockMovie[] = [
  { id: 'film-1', title: 'Film 1', playsOnWeekdays: [0, 2, 4], screeningTimes: ['15:30', '18:00', '20:30'] },
  { id: 'film-2', title: 'Film 2', playsOnWeekdays: [1, 3], screeningTimes: ['17:00', '19:30'] },
  { id: 'film-3', title: 'Film 3', playsOnWeekdays: [5, 6], screeningTimes: ['14:00', '16:30', '19:00', '21:30'] },
  { id: 'film-4', title: 'Film 4', playsOnWeekdays: [0, 1, 2, 3, 4], screeningTimes: ['20:00'] },
  { id: 'film-5', title: 'Film 5', playsOnWeekdays: [2], screeningTimes: ['16:00', '18:30'] },
  { id: 'film-6', title: 'Film 6', playsOnWeekdays: [5], screeningTimes: ['13:30', '18:00', '20:45'] },
]
