"""C05 component tests: Reservation Management (reservations.transition and its wrappers) against a real
throw-away SQLite file, without HTTP.

Derived from the Detailed Design in docs/architecture-and-decisions.md ("C05 - Detailed Design"); the ids
C1-C5 are those of the table in docs/evidence-and-evolution.md ("C05 DD Verification Evidence"). Every test
passes a fixed `at`, so nothing depends on the wall clock.
"""

from __future__ import annotations

import pytest

import cinema.db
from cinema import reservations
from cinema.lifecycle import Command
from cinema.reservations import Conflict, NotFound

STARTS_AT = "2030-01-01T18:00:00Z"
BEFORE_START = "2030-01-01T17:00:00Z"
AFTER_START = "2030-01-01T18:30:00Z"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(cinema.db, "DB_PATH", tmp_path / "cinema.sqlite3")
    cinema.db.reset_db()
    conn = cinema.db.connect()
    yield conn
    conn.close()


def screening(db) -> int:
    """A screening of its own (far from the random demo data), so no other reservation touches its seats."""
    return db.execute(
        "INSERT INTO screenings (movie_id, hall_id, starts_at, ends_at) VALUES (1, 1, ?, '2030-01-01T20:00:00Z')",
        (STARTS_AT,),
    ).lastrowid


def seats(db, n=2) -> list[int]:
    return [r["id"] for r in db.execute("SELECT id FROM seats WHERE hall_id = 1 ORDER BY id LIMIT ?", (n,))]


def plant(db, screening_id, seat_ids, state, hold_until="2030-01-01T17:15:00Z") -> int:
    """Insert a reservation in a given state directly (test setup, not a lifecycle write)."""
    user_id = db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    rid = db.execute(
        "INSERT INTO reservations (user_id, screening_id, state, hold_until) VALUES (?, ?, ?, ?)",
        (user_id, screening_id, state, hold_until),
    ).lastrowid
    db.executemany(
        "INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state) VALUES (?, ?, ?, ?)",
        [(rid, s, screening_id, state) for s in seat_ids],
    )
    return rid


def stored(db, rid):
    """(state, confirmed_at, cancelled_at, [seat states]) exactly as stored."""
    r = db.execute("SELECT state, confirmed_at, cancelled_at FROM reservations WHERE id = ?", (rid,)).fetchone()
    seat_states = [x["state"] for x in db.execute("SELECT state FROM reservation_seats WHERE reservation_id = ?", (rid,))]
    return r["state"], r["confirmed_at"], r["cancelled_at"], seat_states


# C1 - state transition through R5: both tables, the timestamp, committed
def test_approve_writes_both_tables_and_commits(db):
    rid = plant(db, screening(db), seats(db), "PENDING_APPROVAL")

    assert reservations.transition(db, rid, Command.APPROVE, BEFORE_START) == "CONFIRMED"

    other = cinema.db.connect()  # a second connection only sees committed data
    try:
        assert stored(other, rid) == ("CONFIRMED", BEFORE_START, None, ["CONFIRMED", "CONFIRMED"])
    finally:
        other.close()


# C2 - collaboration plan -> _change_state -> unique index -> _guard_br02 (sequence 4.3, R6)
def test_approve_that_loses_br02_changes_nothing(db):
    sc = screening(db)
    seat_x, seat_y = seats(db)
    plant(db, sc, [seat_x], "CONFIRMED")
    rid = plant(db, sc, [seat_x, seat_y], "PENDING_APPROVAL")

    with pytest.raises(Conflict, match="just confirmed by someone else"):
        reservations.transition(db, rid, Command.APPROVE, BEFORE_START)

    assert stored(db, rid) == ("PENDING_APPROVAL", None, None, ["PENDING_APPROVAL", "PENDING_APPROVAL"])
    assert not db.in_transaction


# C3 - failure behaviour: a refusal writes nothing (R7)
def test_refused_command_leaves_the_reservation_untouched(db):
    rid = plant(db, screening(db), seats(db), "CANCELLED")
    db.execute("UPDATE reservations SET cancelled_at = '2030-01-01T10:00:00Z' WHERE id = ?", (rid,))
    before = stored(db, rid)

    with pytest.raises(Conflict, match="Cannot cancel a CANCELLED reservation"):
        reservations.transition(db, rid, Command.CANCEL, BEFORE_START)

    assert stored(db, rid) == before
    assert not db.in_transaction


# C4 - R1 is used by the commands, not only by reads (decision-table row 5); expiry is not written
def test_confirm_of_an_expired_pending_reservation_names_expired(db):
    rid = plant(db, screening(db), seats(db), "PENDING_APPROVAL")

    with pytest.raises(Conflict, match="Cannot confirm a EXPIRED reservation"):
        reservations.confirm(db, rid, AFTER_START)

    assert stored(db, rid)[0] == "PENDING_APPROVAL"


# C5 - precondition: unknown reservation
def test_unknown_reservation_is_not_found(db):
    with pytest.raises(NotFound):
        reservations.transition(db, 999_999, Command.CANCEL, BEFORE_START)
    assert not db.in_transaction
