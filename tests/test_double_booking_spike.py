"""C01 engineering spike -- variant A (Persistence).

Question
--------
When two viewers confirm the SAME seat at the SAME moment, does the database stop
the second one, or does an application-level availability check have to do it?

Method
------
Two real connections to a real SQLite database file, each confirming its own DRAFT
reservation for the same (screening, seat). A threading.Barrier forces both of them
to finish their "is this seat free?" SELECT before either performs its write, which
is exactly the interleaving a premiere on-sale burst produces.

SQLite differs from PostgreSQL in one important way: only one connection may hold
the write lock at a time, so the two writes cannot literally interleave the way they
can under PostgreSQL's READ COMMITTED isolation -- the loser's write blocks (we set
a busy timeout so it waits rather than failing immediately) until the winner commits.
The question the spike asks is unaffected: both threads still decide "the seat is
free" from the same pre-write SELECT, so the outcome still tells us whether that
application-level check is trustworthy under concurrency, or whether the database
constraint is doing the actual work.

The same scenario is run twice: once with the partial unique index dropped, once with
it in place. The difference between the two runs is the answer to the question.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "src" / "cinema" / "schema.sql"

CONFIRMED = "CONFIRMED"
REJECTED_BY_DB = "REJECTED_BY_DB"
REJECTED_BY_APP = "REJECTED_BY_APP"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@pytest.fixture()
def db_path(tmp_path):
    """Fresh schema + one screening, one contested seat, two DRAFT reservations."""
    path = tmp_path / "cinema.sqlite3"
    with _connect(path) as conn:
        conn.executescript(SCHEMA.read_text())
        conn.execute(
            "INSERT INTO users (id, email) VALUES (1, 'a@example.com'), (2, 'b@example.com')"
        )
        conn.execute(
            "INSERT INTO movies (id, title, duration_minutes) VALUES (1, 'Premiere', 135)"
        )
        conn.execute("INSERT INTO halls (id, name) VALUES (1, 'Hall 1')")
        conn.executemany(
            "INSERT INTO seats (id, hall_id, row_label, seat_number) VALUES (?, 1, 'A', ?)",
            [(n, n) for n in range(1, 9)],
        )
        conn.execute(
            """INSERT INTO screenings (id, movie_id, hall_id, starts_at, ends_at)
               VALUES (1, 1, 1, '2026-09-16T18:00:00Z', '2026-09-16T20:15:00Z')"""
        )
        # Both viewers hold seat A5 (id = 5) as DRAFT. Holding is legal for both.
        conn.execute(
            """INSERT INTO reservations (id, user_id, screening_id, state, hold_until)
               VALUES (1, 1, 1, 'DRAFT', '2026-09-16T17:45:00Z'),
                      (2, 2, 1, 'DRAFT', '2026-09-16T17:45:00Z')"""
        )
        conn.execute(
            """INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state)
               VALUES (1, 5, 1, 'DRAFT'), (2, 5, 1, 'DRAFT')"""
        )
    yield path


def _confirm(db_path: Path, reservation_id: int, barrier: threading.Barrier, results: dict) -> None:
    """One viewer pressing 'confirm', guarded the naive way: SELECT, then write."""
    conn = _connect(db_path)
    try:
        taken = conn.execute(
            """SELECT 1 FROM reservation_seats
               WHERE screening_id = 1 AND seat_id = 5 AND state = 'CONFIRMED'"""
        ).fetchone()

        # Both threads have now decided the seat is free.
        barrier.wait(timeout=10)

        if taken:
            results[reservation_id] = REJECTED_BY_APP
            return

        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE reservations SET state = 'CONFIRMED', confirmed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (reservation_id,),
            )
            conn.execute(
                "UPDATE reservation_seats SET state = 'CONFIRMED' WHERE reservation_id = ?",
                (reservation_id,),
            )
            conn.execute("COMMIT")
            results[reservation_id] = CONFIRMED
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            results[reservation_id] = REJECTED_BY_DB
    finally:
        conn.close()


def _race(db_path: Path) -> dict:
    barrier = threading.Barrier(2)
    results: dict = {}
    threads = [
        threading.Thread(target=_confirm, args=(db_path, rid, barrier, results))
        for rid in (1, 2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def _confirmed_rows(db_path: Path) -> int:
    with _connect(db_path) as conn:
        return conn.execute(
            """SELECT count(*) FROM reservation_seats
               WHERE screening_id = 1 AND seat_id = 5 AND state = 'CONFIRMED'"""
        ).fetchone()[0]


def test_without_db_constraint_the_seat_is_sold_twice(db_path):
    """Baseline: the application-level check alone does NOT hold under concurrency."""
    with _connect(db_path) as conn:
        conn.execute("DROP INDEX uq_confirmed_seat_per_screening")

    results = _race(db_path)

    assert results == {1: CONFIRMED, 2: CONFIRMED}, results
    assert _confirmed_rows(db_path) == 2, "expected the double-booking bug to reproduce"


def test_with_db_constraint_the_seat_is_sold_once(db_path):
    """With the partial unique index, exactly one viewer wins and the other is rejected."""
    results = _race(db_path)

    outcomes = sorted(results.values())
    assert outcomes == [CONFIRMED, REJECTED_BY_DB], results
    assert _confirmed_rows(db_path) == 1, "seat A5 must be confirmed exactly once"
