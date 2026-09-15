"""C01 engineering spike -- variant A (Persistence).

Question
--------
When two viewers confirm the SAME seat at the SAME moment, does the database stop
the second one, or does an application-level availability check have to do it?

Method
------
Two real connections to a real PostgreSQL instance, each confirming its own DRAFT
reservation for the same (screening, seat). A threading.Barrier forces both of them
to finish their "is this seat free?" SELECT before either performs its write, which
is exactly the interleaving a premiere on-sale burst produces.

The same scenario is run twice: once with the partial unique index dropped, once with
it in place. The difference between the two runs is the answer to the question.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import psycopg
import pytest

DSN = os.environ.get(
    "CINEMA_DSN", "postgresql://cinema:cinema@localhost:55432/cinema"
)
SCHEMA = Path(__file__).resolve().parents[1] / "src" / "cinema" / "schema.sql"

CONFIRMED = "CONFIRMED"
REJECTED_BY_DB = "REJECTED_BY_DB"
REJECTED_BY_APP = "REJECTED_BY_APP"


@pytest.fixture()
def seeded():
    """Fresh schema + one screening, one contested seat, two DRAFT reservations."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(SCHEMA.read_text())
        conn.execute("INSERT INTO users (email) VALUES ('a@example.com'), ('b@example.com')")
        conn.execute("INSERT INTO movies (title, duration_minutes) VALUES ('Premiere', 135)")
        conn.execute("INSERT INTO halls (name) VALUES ('Hall 1')")
        conn.execute(
            """INSERT INTO seats (hall_id, row_label, seat_number)
               SELECT 1, 'A', n FROM generate_series(1, 8) AS n"""
        )
        conn.execute(
            """INSERT INTO screenings (movie_id, hall_id, starts_at, ends_at)
               VALUES (1, 1, now() + interval '1 day',
                              now() + interval '1 day' + interval '135 minutes')"""
        )
        # Both viewers hold seat A5 (id = 5) as DRAFT. Holding is legal for both.
        conn.execute(
            """INSERT INTO reservations (id, user_id, screening_id, state, hold_until)
               VALUES (1, 1, 1, 'DRAFT', now() + interval '15 minutes'),
                      (2, 2, 1, 'DRAFT', now() + interval '15 minutes')"""
        )
        conn.execute(
            """INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state)
               VALUES (1, 5, 1, 'DRAFT'), (2, 5, 1, 'DRAFT')"""
        )
    yield


def _confirm(reservation_id: int, barrier: threading.Barrier, results: dict) -> None:
    """One viewer pressing 'confirm', guarded the naive way: SELECT, then write."""
    try:
        with psycopg.connect(DSN, autocommit=False) as conn:
            conn.execute("SET statement_timeout = '10s'")

            taken = conn.execute(
                """SELECT 1 FROM reservation_seats
                   WHERE screening_id = 1 AND seat_id = 5 AND state = 'CONFIRMED'"""
            ).fetchone()

            # Both threads have now decided the seat is free.
            barrier.wait(timeout=10)

            if taken:
                results[reservation_id] = REJECTED_BY_APP
                return

            conn.execute(
                "UPDATE reservations SET state = 'CONFIRMED', confirmed_at = now() WHERE id = %s",
                (reservation_id,),
            )
            conn.execute(
                "UPDATE reservation_seats SET state = 'CONFIRMED' WHERE reservation_id = %s",
                (reservation_id,),
            )
            conn.commit()
            results[reservation_id] = CONFIRMED
    except psycopg.errors.UniqueViolation:
        results[reservation_id] = REJECTED_BY_DB


def _race() -> dict:
    barrier = threading.Barrier(2)
    results: dict = {}
    threads = [
        threading.Thread(target=_confirm, args=(rid, barrier, results)) for rid in (1, 2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def _confirmed_rows() -> int:
    with psycopg.connect(DSN, autocommit=True) as conn:
        return conn.execute(
            """SELECT count(*) FROM reservation_seats
               WHERE screening_id = 1 AND seat_id = 5 AND state = 'CONFIRMED'"""
        ).fetchone()[0]


def test_without_db_constraint_the_seat_is_sold_twice(seeded):
    """Baseline: the application-level check alone does NOT hold under concurrency."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP INDEX uq_confirmed_seat_per_screening")

    results = _race()

    assert results == {1: CONFIRMED, 2: CONFIRMED}, results
    assert _confirmed_rows() == 2, "expected the double-booking bug to reproduce"


def test_with_db_constraint_the_seat_is_sold_once(seeded):
    """With the partial unique index, exactly one viewer wins and the other is rejected."""
    results = _race()

    outcomes = sorted(results.values())
    assert outcomes == [CONFIRMED, REJECTED_BY_DB], results
    assert _confirmed_rows() == 1, "seat A5 must be confirmed exactly once"
