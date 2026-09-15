export const SEAT_ROWS = 10
export const SEAT_COLS = 10

export const COLUMN_LETTERS = Array.from({ length: SEAT_COLS }, (_, i) =>
  String.fromCharCode(97 + i),
) // a..j

export function seatId(row: number, colIndex: number): string {
  return `${COLUMN_LETTERS[colIndex]}${row}`
}

/**
 * No-orphan-seat rule (see README): a row must never end up with exactly one free
 * seat isolated between occupied seats. Row edges count as occupied for this check,
 * so `occupied` should be exactly one row's seats, left-to-right.
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

// Deterministic PRNG (mulberry32) so the same seed always lays out the same seats.
function mulberry32(seed: number) {
  let state = seed
  return function random() {
    state |= 0
    state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function hashSeed(text: string): number {
  let hash = 0
  for (let i = 0; i < text.length; i++) {
    hash = (Math.imul(31, hash) + text.charCodeAt(i)) | 0
  }
  return hash
}

/**
 * Placeholder "already taken" seats for a screening - no backend yet, so this
 * stands in for real reservation data. Seeded by movie/date/time so reopening the
 * same screening always shows the same seats, and post-processed so the random
 * draw never bakes in a row that already violates the no-orphan-seat rule.
 */
export function generateMockTakenSeats(seed: string): Set<string> {
  const random = mulberry32(hashSeed(seed))
  const taken = new Set<string>()

  for (let row = 1; row <= SEAT_ROWS; row++) {
    const rowOccupied: boolean[] = Array.from(
      { length: SEAT_COLS },
      () => random() < 0.25,
    )

    // Two passes so a fix on one side can't leave a new orphan on the other.
    for (let pass = 0; pass < 2; pass++) {
      for (let col = 0; col < SEAT_COLS; col++) {
        const prevOccupied = col === 0 || rowOccupied[col - 1]
        const nextOccupied = col === SEAT_COLS - 1 || rowOccupied[col + 1]
        if (!rowOccupied[col] && prevOccupied && nextOccupied) {
          rowOccupied[col] = true
        }
      }
    }

    rowOccupied.forEach((isTaken, col) => {
      if (isTaken) taken.add(seatId(row, col))
    })
  }

  return taken
}
