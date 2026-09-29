// Thin wrapper over the FastAPI backend. In dev, Vite proxies /api to
// http://localhost:8000 (see vite.config.ts), so no CORS setup is needed here.
const BASE_URL = import.meta.env.VITE_API_URL ?? '/api'

export interface User {
  user_id: number
  email: string
}

export interface Movie {
  id: number
  title: string
  duration_minutes: number
  year: number | null
  genres: string[]
  description: string
  poster_url: string | null
  csfd_url: string | null
}

export interface Screening {
  id: number
  movie_id: number
  movie_title: string
  hall_id: number
  hall_name: string
  starts_at: string
  ends_at: string
}

export interface Seat {
  id: number
  row_label: string
  seat_number: number
  occupied: boolean
}

export interface CreatedReservation {
  reservation_id: number
  hold_until: string
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : response.statusText
    throw new ApiError(response.status, detail)
  }
  return response.json() as Promise<T>
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  login: (email: string) => post<User>('/auth/login', { email }),
  movies: () => request<Movie[]>('/movies'),
  screenings: () => request<Screening[]>('/screenings'),
  availability: (screeningId: number) =>
    request<{ screening_id: number; seats: Seat[] }>(`/screenings/${screeningId}/availability`),
  createReservation: (userId: number, screeningId: number, seatIds: number[]) =>
    post<CreatedReservation>('/reservations', {
      user_id: userId,
      screening_id: screeningId,
      seat_ids: seatIds,
    }),
  confirmReservation: (reservationId: number) =>
    post<{ reservation_id: number; state: string }>(`/reservations/${reservationId}/confirm`),
}
