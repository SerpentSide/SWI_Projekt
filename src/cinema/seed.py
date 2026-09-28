"""Random demo screenings and reservations, generated on top of seed.sql.

Every movie gets its own random weekdays and showtimes. Screenings are packed into
halls so two in one hall never overlap (the assumption ADR-004 rests on); a screening
that fits no hall is dropped. Each screening then gets some CONFIRMED reservations,
laid out so no row is left with an orphan seat (ADR-006).
"""

from __future__ import annotations

import random
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")  # showtimes are local; the DB stores UTC
DAYS = 14
CLEANING = timedelta(minutes=20)  # gap between two screenings in one hall
# Possible showtimes: 10:00 .. 21:30 in 15-minute steps.
SLOTS = [time(h, m) for h in range(10, 22) for m in (0, 15, 30, 45)]


def iso(dt: datetime) -> str:
    """The one timestamp format stored in the DB: ISO-8601 UTC, e.g. 2026-09-15T18:00:00Z.

    Fixed width, so plain string comparison orders timestamps correctly.
    """
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_random(conn: sqlite3.Connection, rng: random.Random | None = None) -> None:
    rng = rng or random.Random()
    movies = conn.execute("SELECT id, duration_minutes FROM movies").fetchall()
    halls = [row[0] for row in conn.execute("SELECT id FROM halls ORDER BY id")]
    users = [row[0] for row in conn.execute("SELECT id FROM users WHERE email <> 'demo@example.com'")]
    now = iso(datetime.now(UTC))

    # 1. A weekly schedule per movie: 2-4 weekdays, 1-3 showtimes.
    schedule = {
        movie_id: (
            set(rng.sample(range(7), rng.randint(2, 4))),
            sorted(rng.sample(SLOTS, rng.randint(1, 3))),
            timedelta(minutes=duration),
        )
        for movie_id, duration in movies
    }

    # 2. Expand it over the next DAYS days and pack each day into halls.
    screenings = []  # (movie_id, hall_id, starts_at, ends_at)
    today = date.today()
    for offset in range(DAYS):
        day = today + timedelta(days=offset)
        wanted = [
            (datetime.combine(day, t, TZ), movie_id, duration)
            for movie_id, (weekdays, times, duration) in schedule.items()
            if day.weekday() in weekdays
            for t in times
        ]
        hall_free_at = {hall: datetime.min.replace(tzinfo=TZ) for hall in halls}
        for start, movie_id, duration in sorted(wanted):
            hall = next((h for h in halls if hall_free_at[h] <= start), None)
            if hall is None:
                continue
            hall_free_at[hall] = start + duration + CLEANING
            screenings.append((movie_id, hall, start, start + duration))

    # 3. Insert screenings, then random confirmed reservations for each.
    seats_by_hall: dict[int, dict[str, list[int]]] = {}
    for seat_id, hall_id, row_label in conn.execute(
        "SELECT id, hall_id, row_label FROM seats ORDER BY hall_id, row_label, seat_number"
    ):
        seats_by_hall.setdefault(hall_id, {}).setdefault(row_label, []).append(seat_id)

    for movie_id, hall_id, starts_at, ends_at in screenings:
        screening_id = conn.execute(
            "INSERT INTO screenings (movie_id, hall_id, starts_at, ends_at) VALUES (?, ?, ?, ?)",
            (movie_id, hall_id, iso(starts_at), iso(ends_at)),
        ).lastrowid

        fill = rng.uniform(0.1, 0.6)  # how full this screening is
        for row in seats_by_hall[hall_id].values():
            for block in taken_blocks(row, fill, rng):
                reservation_id = conn.execute(
                    """INSERT INTO reservations
                           (user_id, screening_id, state, hold_until, confirmed_at)
                       VALUES (?, ?, 'CONFIRMED', ?, ?)""",
                    (rng.choice(users), screening_id, now, now),
                ).lastrowid
                conn.executemany(
                    """INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state)
                       VALUES (?, ?, ?, 'CONFIRMED')""",
                    [(reservation_id, seat_id, screening_id) for seat_id in block],
                )


def taken_blocks(row: list[int], fill: float, rng: random.Random) -> list[list[int]]:
    """Randomly occupy seats of one row, then close every orphan gap.

    Returns the occupied seats as contiguous blocks - each block becomes one reservation.
    """
    taken = [rng.random() < fill for _ in row]
    # Filling a lone gap never creates a new one (its neighbours are already taken),
    # so a single left-to-right pass is enough.
    for i in range(len(row)):
        left = i == 0 or taken[i - 1]
        right = i == len(row) - 1 or taken[i + 1]
        if not taken[i] and left and right:
            taken[i] = True

    blocks, current = [], []
    for seat_id, is_taken in zip(row, taken):
        if is_taken:
            current.append(seat_id)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks
