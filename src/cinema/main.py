"""Cinema seat reservation API -- deliberately one module, plain SQL, no ORM.

Run:  uvicorn cinema.main:app --app-dir src --reload
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date as Date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from cinema.notifications import BrowserNotifications, NotificationService
from cinema.seed import TZ, iso, seed_random

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("CINEMA_DB", HERE.parents[1] / "cinema.sqlite3"))
HOLD = timedelta(minutes=15)
log = logging.getLogger("cinema")


# --- database ---------------------------------------------------------------


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Autocommit connection; writes open their own transaction via `write_tx`."""
    conn = sqlite3.connect(path or DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = lambda cursor, row: {col[0]: value for col, value in zip(cursor.description, row)}
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def write_tx(db: sqlite3.Connection):
    """BEGIN IMMEDIATE takes SQLite's single write lock up front, so the checks and the
    writes inside one request can't interleave with another writer (ADR-002, ADR-004)."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def reset_db(path: Path | None = None) -> None:
    """Drop everything and load schema + demo data (random screenings and reservations)."""
    conn = connect(path)
    conn.row_factory = None
    try:
        conn.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
        conn.executescript((HERE / "seed.sql").read_text(encoding="utf-8"))
        with write_tx(conn):
            seed_random(conn)
    finally:
        conn.close()


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


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def now() -> str:
    return iso(datetime.now(UTC))


# --- notifications (external boundary, see cinema/notifications.py) --------

notifier: NotificationService = BrowserNotifications()


def notify(event: str, *args) -> None:
    """Call notifier.<event>(db, *args) AFTER the business transaction has committed.

    Any failure is logged and swallowed: a failed notification never fails a reservation
    (Project Frame, external boundary)."""
    try:
        conn = connect()
        try:
            getattr(notifier, event)(conn, *args)
        finally:
            conn.close()
    except Exception:
        log.exception("notification %s%r failed", event, args)


# The ONE place that defines "occupied" (ADR-003): confirmed, or a draft whose hold
# is still running. Every availability check goes through this.
# v0.2: a reservation waiting for approval also blocks its seats, until the screening starts.
OCCUPIED_SEATS_SQL = """
    SELECT rs.seat_id
    FROM reservation_seats rs
    JOIN reservations r ON r.id = rs.reservation_id
    JOIN screenings sc ON sc.id = r.screening_id
    WHERE rs.screening_id = ?
      AND (r.state = 'CONFIRMED'
           OR (r.state = 'DRAFT' AND r.hold_until > ?)
           OR (r.state = 'PENDING_APPROVAL' AND sc.starts_at > ?))
"""


def occupied_seat_ids(db, screening_id: int) -> set[int]:
    at = now()
    return {row["seat_id"] for row in db.execute(OCCUPIED_SEATS_SQL, (screening_id, at, at))}


def set_state(db, reservation_id: int, state: str, timestamp_column: str | None = None) -> None:
    """Change a reservation's state in BOTH tables (ADR-005)."""
    extra = f", {timestamp_column} = ?" if timestamp_column else ""
    params = (state, now(), reservation_id) if timestamp_column else (state, reservation_id)
    db.execute(f"UPDATE reservations SET state = ?{extra} WHERE id = ?", params)
    db.execute("UPDATE reservation_seats SET state = ? WHERE reservation_id = ?", (state, reservation_id))


def has_isolated_free_seat(occupied: list[bool]) -> bool:
    """No-orphan rule (ADR-006): exactly one free seat between occupied ones; row ends count as occupied."""
    run = 0
    for taken in occupied + [True]:
        if taken:
            if run == 1:
                return True
            run = 0
        else:
            run += 1
    return False


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


def get_reservation_or_404(db, reservation_id: int) -> dict:
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None:
        raise HTTPException(404, "Reservation not found")
    return reservation


def is_expired(reservation: dict) -> bool:
    return reservation["state"] == "DRAFT" and reservation["hold_until"] <= now()


def effective_state(db, reservation: dict) -> str:
    """The state as a client sees it: expiry is derived, never stored by a read (ADR-003).

    A DRAFT past its hold, or a PENDING_APPROVAL whose screening has started, reads as EXPIRED."""
    if is_expired(reservation):
        return "EXPIRED"
    if reservation["state"] == "PENDING_APPROVAL":
        screening = get_screening_or_404(db, reservation["screening_id"])
        if screening["starts_at"] <= now():
            return "EXPIRED"
    return reservation["state"]


def needs_approval(db, reservation_id: int) -> bool:
    return db.execute(
        """SELECT 1 FROM reservation_seats rs JOIN seats s ON s.id = rs.seat_id
           WHERE rs.reservation_id = ? AND s.requires_approval = 1""",
        (reservation_id,),
    ).fetchone() is not None


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
        db.execute("INSERT INTO users (email) VALUES (?) ON CONFLICT (email) DO NOTHING", (email,))
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
    notify("new_movie", movie_id)
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
def availability(screening_id: int, db=Depends(get_db)):
    screening = get_screening_or_404(db, screening_id)
    occupied = occupied_seat_ids(db, screening_id)
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
    with write_tx(db):
        screening = get_screening_or_404(db, body.screening_id)
        if screening["starts_at"] <= now():  # BR-01: reservable only while now < starts_at
            raise HTTPException(409, "The screening has already started")
        if len(set(body.seat_ids)) != len(body.seat_ids):
            raise HTTPException(422, "Duplicate seat ids")
        if db.execute("SELECT 1 FROM users WHERE id = ?", (body.user_id,)).fetchone() is None:
            raise HTTPException(404, "User not found")

        hall_seats = db.execute(
            "SELECT id, row_label, seat_number FROM seats WHERE hall_id = ?", (screening["hall_id"],)
        ).fetchall()
        seats_by_id = {s["id"]: s for s in hall_seats}
        requested = set(body.seat_ids)

        unknown = requested - seats_by_id.keys()
        if unknown:
            raise HTTPException(404, f"Seats not in this screening's hall: {sorted(unknown)}")

        occupied = occupied_seat_ids(db, body.screening_id)
        taken = requested & occupied
        if taken:
            raise HTTPException(409, f"Seats already taken: {sorted(taken)}")

        # No-orphan rule, checked for every row the selection touches.
        rows = defaultdict(list)
        for seat in hall_seats:
            rows[seat["row_label"]].append(seat)
        for label in {seats_by_id[i]["row_label"] for i in requested}:
            row = sorted(rows[label], key=lambda s: s["seat_number"])
            if has_isolated_free_seat([s["id"] in occupied or s["id"] in requested for s in row]):
                raise HTTPException(422, f"Selection would leave a single isolated free seat in row {label}")

        hold_until = iso(datetime.now(UTC) + HOLD)
        reservation_id = db.execute(
            "INSERT INTO reservations (user_id, screening_id, state, hold_until) VALUES (?, ?, 'DRAFT', ?)",
            (body.user_id, body.screening_id, hold_until),
        ).lastrowid
        db.executemany(
            "INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state) VALUES (?, ?, ?, 'DRAFT')",
            [(reservation_id, seat_id, body.screening_id) for seat_id in sorted(requested)],
        )
    return {"reservation_id": reservation_id, "hold_until": hold_until}


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: int, db=Depends(get_db)):
    reservation = get_reservation_or_404(db, reservation_id)
    reservation["state"] = effective_state(db, reservation)  # lazy expiry (ADR-003)
    reservation["seats"] = db.execute(
        """SELECT s.id, s.row_label, s.seat_number
           FROM reservation_seats rs JOIN seats s ON s.id = rs.seat_id
           WHERE rs.reservation_id = ? ORDER BY s.row_label, s.seat_number""",
        (reservation_id,),
    ).fetchall()
    return reservation


@app.get("/users/{user_id}/reservations")
def list_user_reservations(user_id: int, db=Depends(get_db)):
    reservations = db.execute(
        "SELECT * FROM reservations WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    for r in reservations:
        r["state"] = effective_state(db, r)
    return reservations


@app.post("/reservations/{reservation_id}/confirm")
def confirm_reservation(reservation_id: int, db=Depends(get_db)):
    expired = False
    new_state = "CONFIRMED"
    try:
        with write_tx(db):
            reservation = get_reservation_or_404(db, reservation_id)
            if is_expired(reservation):
                set_state(db, reservation_id, "EXPIRED")
                expired = True
            elif reservation["state"] != "DRAFT":
                raise HTTPException(409, f"Cannot confirm a {reservation['state']} reservation")
            elif needs_approval(db, reservation_id):  # v0.2: wait for a box office decision
                new_state = "PENDING_APPROVAL"
                set_state(db, reservation_id, new_state)
            else:
                set_state(db, reservation_id, new_state, "confirmed_at")
    except sqlite3.IntegrityError:  # the DB invariant (ADR-004)
        raise HTTPException(409, "One of the seats was just confirmed by someone else")
    if expired:  # raised after the commit, so the EXPIRED state is kept
        raise HTTPException(409, "Reservation hold has expired")
    # OP-03 step 5, after the commit.
    if new_state == "CONFIRMED":
        notify("reservation_confirmed", reservation_id)
    else:
        notify("reservation_pending_approval", reservation_id)
    return {"reservation_id": reservation_id, "state": new_state}


@app.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int, db=Depends(get_db)):
    with write_tx(db):
        reservation = get_reservation_or_404(db, reservation_id)
        if reservation["state"] not in ("DRAFT", "PENDING_APPROVAL", "CONFIRMED") or is_expired(reservation):
            raise HTTPException(409, "Only active reservations can be cancelled")
        screening = get_screening_or_404(db, reservation["screening_id"])
        if screening["starts_at"] <= now():  # BR-03: DRAFT and CONFIRMED alike
            raise HTTPException(409, "The screening has already started")
        set_state(db, reservation_id, "CANCELLED", "cancelled_at")
    return {"reservation_id": reservation_id, "state": "CANCELLED"}


@app.post("/reservations/{reservation_id}/decision")
def decide_reservation(reservation_id: int, body: DecisionIn, db=Depends(get_db)):
    """v0.2 OP-05: a box office operator approves or rejects a PENDING_APPROVAL reservation.

    Who counts as an operator is not checked yet - the application has no roles (left for C03)."""
    try:
        with write_tx(db):
            reservation = get_reservation_or_404(db, reservation_id)
            if reservation["state"] != "PENDING_APPROVAL":
                raise HTTPException(409, f"Cannot decide a {effective_state(db, reservation)} reservation")
            if get_screening_or_404(db, reservation["screening_id"])["starts_at"] <= now():
                raise HTTPException(409, "The screening has started, the approval has expired")
            if body.decision == "approve":
                set_state(db, reservation_id, "CONFIRMED", "confirmed_at")
            else:
                set_state(db, reservation_id, "REJECTED")
    except sqlite3.IntegrityError:  # the DB invariant (ADR-004)
        raise HTTPException(409, "One of the seats was just confirmed by someone else")
    approved = body.decision == "approve"
    notify("reservation_approved" if approved else "reservation_rejected", reservation_id)
    return {"reservation_id": reservation_id, "state": "CONFIRMED" if approved else "REJECTED"}


@app.post("/users/{user_id}/notifications/deliver")
def deliver_notifications(user_id: int, db=Depends(get_db)):
    """The browser's poll: return the user's undelivered notifications and mark them
    delivered, so each one pops up once. POST because it changes state."""
    with write_tx(db):
        rows = db.execute(
            """SELECT id, kind, title, body, created_at FROM notifications
               WHERE user_id = ? AND delivered_at IS NULL ORDER BY id""",
            (user_id,),
        ).fetchall()
        if rows:
            db.execute(
                "UPDATE notifications SET delivered_at = ? WHERE user_id = ? AND delivered_at IS NULL AND id <= ?",
                (now(), user_id, rows[-1]["id"]),
            )
    return rows


if __name__ == "__main__":  # PYTHONPATH=src python -m cinema.main  -> wipe DB, reseed
    reset_db()
    print(f"Database {DB_PATH} reset and seeded.")
