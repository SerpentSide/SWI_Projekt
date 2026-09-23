export const WEEKDAY_LABELS = ['Po', 'Út', 'St', 'Čt', 'Pá', 'So', 'Ne'] as const

export const MONTH_NAMES = [
  'Leden',
  'Únor',
  'Březen',
  'Duben',
  'Květen',
  'Červen',
  'Červenec',
  'Srpen',
  'Září',
  'Říjen',
  'Listopad',
  'Prosinec',
] as const

/** Monday-first weekday index (0 = Po ... 6 = Ne) for a given date. */
export function mondayIndex(date: Date): number {
  return (date.getDay() + 6) % 7
}

export interface MonthCell {
  day: number
  date: Date
}

/**
 * A full month as weeks of 7 cells (Monday-first). Cells outside the month
 * (the lead-in/lead-out days needed to complete the first/last week) are null.
 */
export function getMonthWeeks(year: number, month: number): (MonthCell | null)[][] {
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const firstWeekday = mondayIndex(new Date(year, month, 1))

  const cells: (MonthCell | null)[] = []
  for (let i = 0; i < firstWeekday; i++) cells.push(null)
  for (let day = 1; day <= daysInMonth; day++) {
    cells.push({ day, date: new Date(year, month, day) })
  }
  while (cells.length % 7 !== 0) cells.push(null)

  const weeks: (MonthCell | null)[][] = []
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))
  return weeks
}

export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}
