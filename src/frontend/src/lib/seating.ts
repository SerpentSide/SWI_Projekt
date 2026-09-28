/**
 * No-orphan-seat rule (see README): a row must never end up with exactly one free
 * seat isolated between occupied seats. Row edges count as occupied for this check,
 * so `occupied` should be exactly one row's seats, left-to-right.
 *
 * The backend enforces the same rule; this copy only gives instant feedback.
 */
export function hasIsolatedFreeSeat(occupied: boolean[]): boolean {
  let freeRunLength = 0
  for (let i = 0; i <= occupied.length; i++) {
    const isBoundaryOrOccupied = i === occupied.length || occupied[i]
    if (isBoundaryOrOccupied) {
      if (freeRunLength === 1) return true
      freeRunLength = 0
    } else {
      freeRunLength++
    }
  }
  return false
}
