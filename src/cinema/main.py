"""Cinema seat reservation API -- the HTTP layer (plain SQL, no ORM).

Routes, request validation and the mapping of domain errors to status codes. Reservation lifecycle
rules live in `reservations.py` (ADR-008), "occupied" in `availability.py`, notifications in
`notifications.py`, the connection and transaction in `db.py`. The movie/screening/login reads and
the movie insert below are catalogue data, not part of the reservation lifecycle.

Run:  uvicorn cinema.main:app --app-dir src --reload
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, time, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cinema import availability, notifications, reservations
from cinema.db import DB_PATH, connect, now, reset_db, write_tx  # noqa: F401  (DB_PATH, reset_db: `python -m cinema.main`)
from cinema.seed import TZ, iso


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create and seed the database on first start; leave existing data alone."""
    conn = connect()
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'movies'").fetchone()
    finally:
        conn.close()
    if exists is None:
        reset_db()
    yield


app = FastAPI(title="Cinema Seat Reservation", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(reservations.ReservationError)
def reservation_error(_request: Request, exc: reservations.ReservationError):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


SCREENING_SQL = """
    SELECT s.id, s.movie_id, m.title AS movie_title, s.hall_id, h.name AS hall_name,
           s.starts_at, s.ends_at
    FROM screenings s JOIN movies m ON m.id = s.movie_id JOIN halls h ON h.id = s.hall_id
"""


def get_screening_or_404(db, screening_id: int) -> dict:
    screening = db.execute(SCREENING_SQL + " WHERE s.id = ?", (screening_id,)).fetchone()
    if screening is None:
        raise HTTPException(404, "Screening not found")
    return screening


# --- schemas ----------------------------------------------------------------


class LoginIn(BaseModel):
    email: str


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]


class MovieIn(BaseModel):
    title: str = Field(min_length=1)
    duration_minutes: int = Field(gt=0)
    year: int | None = None
    genres: list[str] = []
    description: str = ""
    poster_url: str | None = None
    csfd_url: str | None = None


class ReservationIn(BaseModel):
    user_id: int
    screening_id: int
    seat_ids: list[int] = Field(min_length=1)


# --- endpoints --------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/login")
def login(body: LoginIn, db=Depends(get_db)):
    """No passwords yet: an email identifies the user, created on first login."""
    email = body.email.strip().lower()
    with write_tx(db):
        # OR IGNORE, not ON CONFLICT ... DO NOTHING: that upsert syntax needs SQLite >= 3.24
        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
    user = db.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
    return {"user_id": user["id"], "email": user["email"]}


@app.get("/movies")
def list_movies(db=Depends(get_db)):
    movies = db.execute(
        """SELECT id, title, duration_minutes, year, genres, description, poster_url, csfd_url
           FROM movies ORDER BY id"""
    ).fetchall()
    for movie in movies:
        movie["genres"] = json.loads(movie["genres"])
    return movies


@app.post("/movies", status_code=201)
def add_movie(body: MovieIn, db=Depends(get_db)):
    """Add a movie to the programme; every user is notified about it.

    Like /decision, who may call this is not checked yet - the application has no roles."""
    with write_tx(db):
        movie_id = db.execute(
            """INSERT INTO movies (title, duration_minutes, year, genres, description, poster_url, csfd_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (body.title, body.duration_minutes, body.year, json.dumps(body.genres, ensure_ascii=False),
             body.description, body.poster_url, body.csfd_url),
        ).lastrowid
    notifications.notify("new_movie", movie_id)
    return {"id": movie_id, **body.model_dump()}


@app.get("/screenings")
def list_screenings(movie_id: int | None = None, date: Date | None = None, db=Depends(get_db)):
    """Optional filters: ?movie_id=1&date=2026-09-28 (a local, Prague calendar day)."""
    sql, params = SCREENING_SQL + " WHERE 1 = 1", []
    if movie_id is not None:
        sql += " AND s.movie_id = ?"
        params.append(movie_id)
    if date is not None:
        start = datetime.combine(date, time(), TZ)
        sql += " AND s.starts_at >= ? AND s.starts_at < ?"
        params += [iso(start), iso(start + timedelta(days=1))]
    return db.execute(sql + " ORDER BY s.starts_at", params).fetchall()


@app.get("/screenings/{screening_id}")
def get_screening(screening_id: int, db=Depends(get_db)):
    return get_screening_or_404(db, screening_id)


@app.get("/screenings/{screening_id}/availability")
def screening_availability(screening_id: int, db=Depends(get_db)):
    screening = get_screening_or_404(db, screening_id)
    occupied = availability.occupied_seat_ids(db, screening_id, now())
    seats = db.execute(
        "SELECT id, row_label, seat_number FROM seats WHERE hall_id = ? ORDER BY row_label, seat_number",
        (screening["hall_id"],),
    ).fetchall()
    return {
        "screening_id": screening_id,
        "seats": [{**seat, "occupied": seat["id"] in occupied} for seat in seats],
    }


@app.post("/reservations", status_code=201)
def create_reservation(body: ReservationIn, db=Depends(get_db)):
    return reservations.create(db, body.user_id, body.screening_id, body.seat_ids, now())


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: int, db=Depends(get_db)):
    return reservations.get(db, reservation_id, now())


@app.get("/users/{user_id}/reservations")
def list_user_reservations(user_id: int, db=Depends(get_db)):
    return reservations.list_for_user(db, user_id, now())


@app.post("/reservations/{reservation_id}/confirm")
def confirm_reservation(reservation_id: int, db=Depends(get_db)):
    state = reservations.confirm(db, reservation_id, now())
    # OP-03 step 5, after the commit; Reservation Management does not know about notifications.
    notifications.notify("reservation_confirmed" if state == "CONFIRMED" else "reservation_pending_approval",
                         reservation_id)
    return {"reservation_id": reservation_id, "state": state}


@app.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int, db=Depends(get_db)):
    return {"reservation_id": reservation_id, "state": reservations.cancel(db, reservation_id, now())}


@app.post("/reservations/{reservation_id}/decision")
def decide_reservation(reservation_id: int, body: DecisionIn, db=Depends(get_db)):
    """v0.2 OP-05: a box office operator approves or rejects a PENDING_APPROVAL reservation.

    Who counts as an operator is not checked yet - the application has no roles."""
    state = reservations.decide(db, reservation_id, body.decision, now())
    notifications.notify("reservation_approved" if state == "CONFIRMED" else "reservation_rejected", reservation_id)
    return {"reservation_id": reservation_id, "state": state}


@app.post("/users/{user_id}/notifications/deliver")
def deliver_notifications(user_id: int, db=Depends(get_db)):
    """The browser's poll. POST because it changes state (marks the messages delivered)."""
    return notifications.deliver(db, user_id)


if __name__ == "__main__":  # PYTHONPATH=src python -m cinema.main  -> wipe DB, reseed
    reset_db()
    print(f"Database {DB_PATH} reset and seeded.")
