"""Reservation Management: the ONLY module that changes a Reservation's state (ADR-008).

It owns the lifecycle (create, confirm, cancel, approve/reject, derived expiry), the transaction around
each change, and the BR-02 decision: when the database refuses a second CONFIRMED seat, the whole
transaction is rolled back and the caller gets a `Conflict`. It knows nothing about HTTP or notifications:
errors are `ReservationError`s, which the API maps to status codes, and the API asks for a notification
after the command has returned. Whether a transition is allowed, and into which state, is decided by the
pure lifecycle policy in `lifecycle.py` (C05); this module loads its facts and performs its answer.

Times are passed in as `at` (an ISO string from `db.now`) so one request uses one instant."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Literal

from cinema import lifecycle
from cinema.availability import occupied_seat_ids, orphaned_row
from cinema.db import write_tx
from cinema.lifecycle import Command, Refused, ReservationFacts, Transition
from cinema.seed import iso

HOLD = timedelta(minutes=15)


class ReservationError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFound(ReservationError):
    status_code = 404


class Conflict(ReservationError):
    status_code = 409


class Invalid(ReservationError):
    status_code = 422


# --- reading ------------------------------------------------------------------


def _screening(db, screening_id: int) -> dict:
    screening = db.execute(
        "SELECT id, hall_id, starts_at FROM screenings WHERE id = ?", (screening_id,)
    ).fetchone()
    if screening is None:
        raise NotFound("Screening not found")
    return screening


def _reservation(db, reservation_id: int) -> dict:
    reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
    if reservation is None:
        raise NotFound("Reservation not found")
    return reservation


def needs_approval(db, reservation_id: int) -> bool:
    return db.execute(
        """SELECT 1 FROM reservation_seats rs JOIN seats s ON s.id = rs.seat_id
           WHERE rs.reservation_id = ? AND s.requires_approval = 1""",
        (reservation_id,),
    ).fetchone() is not None


def _facts(db, reservation: dict) -> ReservationFacts:
    """The facts the lifecycle policy judges, read from where they are stored."""
    return ReservationFacts(
        stored_state=reservation["state"],
        hold_until=reservation["hold_until"],
        starts_at=_screening(db, reservation["screening_id"])["starts_at"],
        needs_approval=needs_approval(db, reservation["id"]),
    )


def _load_facts(db, reservation_id: int) -> ReservationFacts:
    return _facts(db, _reservation(db, reservation_id))


def effective_state(db, reservation: dict, at: str) -> str:
    """The state as a client sees it: expiry is derived, never stored by a read (ADR-003)."""
    return lifecycle.effective_state(_facts(db, reservation), at)


def get(db, reservation_id: int, at: str) -> dict:
    reservation = _reservation(db, reservation_id)
    reservation["state"] = effective_state(db, reservation, at)  # lazy expiry (ADR-003)
    reservation["seats"] = db.execute(
        """SELECT s.id, s.row_label, s.seat_number
           FROM reservation_seats rs JOIN seats s ON s.id = rs.seat_id
           WHERE rs.reservation_id = ? ORDER BY s.row_label, s.seat_number""",
        (reservation_id,),
    ).fetchall()
    return reservation


def list_for_user(db, user_id: int, at: str) -> list[dict]:
    reservations = db.execute(
        "SELECT * FROM reservations WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    for r in reservations:
        r["state"] = effective_state(db, r, at)
    return reservations


# --- changing state -----------------------------------------------------------


def _change_state(db, reservation_id: int, transition: Transition, at: str) -> None:
    """The one place that writes a Reservation's state: BOTH tables (ADR-005), inside a transaction.

    SQLite lets COMMIT through after a failed statement, so a state change outside `write_tx` could
    leave the two tables apart (Part A3, A8); refuse to run without one."""
    if not db.in_transaction:
        raise RuntimeError("Reservation state may only change inside write_tx")
    state, column = transition.target, transition.timestamp_column
    extra = f", {column} = ?" if column else ""
    params = (state, at, reservation_id) if column else (state, reservation_id)
    db.execute(f"UPDATE reservations SET state = ?{extra} WHERE id = ?", params)
    db.execute("UPDATE reservation_seats SET state = ? WHERE reservation_id = ?", (state, reservation_id))


@contextmanager
def _guard_br02():
    """BR-02 decision: the unique index refused a second CONFIRMED seat -> the caller's transaction rolls back."""
    try:
        yield
    except sqlite3.IntegrityError:  # the DB invariant (ADR-004)
        raise Conflict("One of the seats was just confirmed by someone else") from None


def create(db, user_id: int, screening_id: int, seat_ids: list[int], at: str) -> dict:
    with write_tx(db):
        screening = _screening(db, screening_id)
        if screening["starts_at"] <= at:  # BR-01: reservable only while now < starts_at
            raise Conflict("The screening has already started")
        if len(set(seat_ids)) != len(seat_ids):
            raise Invalid("Duplicate seat ids")
        if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise NotFound("User not found")

        hall_seats = db.execute(
            "SELECT id, row_label, seat_number FROM seats WHERE hall_id = ?", (screening["hall_id"],)
        ).fetchall()
        requested = set(seat_ids)

        unknown = requested - {s["id"] for s in hall_seats}
        if unknown:
            raise NotFound(f"Seats not in this screening's hall: {sorted(unknown)}")

        occupied = occupied_seat_ids(db, screening_id, at)
        taken = requested & occupied
        if taken:
            raise Conflict(f"Seats already taken: {sorted(taken)}")

        label = orphaned_row(hall_seats, occupied, requested)  # no-orphan rule, BR-04
        if label is not None:
            raise Invalid(f"Selection would leave a single isolated free seat in row {label}")

        hold_until = iso(datetime.fromisoformat(at) + HOLD)
        reservation_id = db.execute(
            "INSERT INTO reservations (user_id, screening_id, state, hold_until) VALUES (?, ?, 'DRAFT', ?)",
            (user_id, screening_id, hold_until),
        ).lastrowid
        db.executemany(
            "INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state) VALUES (?, ?, ?, 'DRAFT')",
            [(reservation_id, seat_id, screening_id) for seat_id in sorted(requested)],
        )
    return {"reservation_id": reservation_id, "hold_until": hold_until}


def transition(db, reservation_id: int, command: Command, at: str) -> str:
    """The one command path for an existing Reservation (C05 DD 3.3). Returns the new stored state.

    `_guard_br02` sits outside `write_tx`, so the rollback has run before a `Conflict` leaves (DD 4.3)."""
    with _guard_br02():
        with write_tx(db):
            decision = lifecycle.plan(command, _load_facts(db, reservation_id), at)
            if isinstance(decision, Refused):
                raise Conflict(decision.reason)
            _change_state(db, reservation_id, decision, at)
    return decision.target


def confirm(db, reservation_id: int, at: str) -> str:
    """OP-03: DRAFT -> CONFIRMED, or -> PENDING_APPROVAL when a seat needs approval."""
    return transition(db, reservation_id, Command.CONFIRM, at)


def cancel(db, reservation_id: int, at: str) -> str:
    """OP-04: DRAFT / PENDING_APPROVAL / CONFIRMED -> CANCELLED, before the screening starts."""
    return transition(db, reservation_id, Command.CANCEL, at)


def decide(db, reservation_id: int, decision: Literal["approve", "reject"], at: str) -> str:
    """OP-05: a box office operator approves or rejects a PENDING_APPROVAL reservation.

    Who counts as an operator is not checked yet - the application has no roles."""
    return transition(db, reservation_id, Command(decision), at)
