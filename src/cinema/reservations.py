"""Reservation Management: the ONLY module that changes a Reservation's state (ADR-008).

It owns the lifecycle (create, confirm, cancel, approve/reject, derived expiry), the transaction around
each change, and the BR-02 decision: when the database refuses a second CONFIRMED seat, the whole
transaction is rolled back and the caller gets a `Conflict`. It knows nothing about HTTP or notifications:
errors are `ReservationError`s, which the API maps to status codes, and the API asks for a notification
after the command has returned.

Times are passed in as `at` (an ISO string from `db.now`) so one request uses one instant."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Literal

from cinema.availability import occupied_seat_ids, orphaned_row
from cinema.db import write_tx
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


def is_expired(reservation: dict, at: str) -> bool:
    return reservation["state"] == "DRAFT" and reservation["hold_until"] <= at


def effective_state(db, reservation: dict, at: str) -> str:
    """The state as a client sees it: expiry is derived, never stored by a read (ADR-003).

    A DRAFT past its hold, or a PENDING_APPROVAL whose screening has started, reads as EXPIRED."""
    if is_expired(reservation, at):
        return "EXPIRED"
    if reservation["state"] == "PENDING_APPROVAL" and _screening(db, reservation["screening_id"])["starts_at"] <= at:
        return "EXPIRED"
    return reservation["state"]


def needs_approval(db, reservation_id: int) -> bool:
    return db.execute(
        """SELECT 1 FROM reservation_seats rs JOIN seats s ON s.id = rs.seat_id
           WHERE rs.reservation_id = ? AND s.requires_approval = 1""",
        (reservation_id,),
    ).fetchone() is not None


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


def _change_state(db, reservation_id: int, state: str, at: str, timestamp_column: str | None = None) -> None:
    """The one place that writes a Reservation's state: BOTH tables (ADR-005), inside a transaction.

    SQLite lets COMMIT through after a failed statement, so a state change outside `write_tx` could
    leave the two tables apart (Part A3, A8); refuse to run without one."""
    if not db.in_transaction:
        raise RuntimeError("Reservation state may only change inside write_tx")
    extra = f", {timestamp_column} = ?" if timestamp_column else ""
    params = (state, at, reservation_id) if timestamp_column else (state, reservation_id)
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


def confirm(db, reservation_id: int, at: str) -> str:
    """OP-03: DRAFT -> CONFIRMED, or -> PENDING_APPROVAL when a seat needs approval. Returns the new state."""
    with _guard_br02():
        with write_tx(db):
            reservation = _reservation(db, reservation_id)
            if is_expired(reservation, at):  # derived, not stored (ADR-003): the row stays DRAFT
                raise Conflict("Reservation hold has expired")
            if reservation["state"] != "DRAFT":
                raise Conflict(f"Cannot confirm a {reservation['state']} reservation")
            if needs_approval(db, reservation_id):  # v0.2: wait for a box office decision
                new_state = "PENDING_APPROVAL"
                _change_state(db, reservation_id, new_state, at)
            else:
                new_state = "CONFIRMED"
                _change_state(db, reservation_id, new_state, at, "confirmed_at")
    return new_state


def cancel(db, reservation_id: int, at: str) -> str:
    with write_tx(db):
        reservation = _reservation(db, reservation_id)
        if reservation["state"] not in ("DRAFT", "PENDING_APPROVAL", "CONFIRMED") or is_expired(reservation, at):
            raise Conflict("Only active reservations can be cancelled")
        if _screening(db, reservation["screening_id"])["starts_at"] <= at:  # BR-03: DRAFT and CONFIRMED alike
            raise Conflict("The screening has already started")
        _change_state(db, reservation_id, "CANCELLED", at, "cancelled_at")
    return "CANCELLED"


def decide(db, reservation_id: int, decision: Literal["approve", "reject"], at: str) -> str:
    """OP-05: a box office operator approves or rejects a PENDING_APPROVAL reservation.

    Who counts as an operator is not checked yet - the application has no roles."""
    with _guard_br02():
        with write_tx(db):
            reservation = _reservation(db, reservation_id)
            if reservation["state"] != "PENDING_APPROVAL":
                raise Conflict(f"Cannot decide a {effective_state(db, reservation, at)} reservation")
            if _screening(db, reservation["screening_id"])["starts_at"] <= at:
                raise Conflict("The screening has started, the approval has expired")
            if decision == "approve":
                _change_state(db, reservation_id, "CONFIRMED", at, "confirmed_at")
            else:
                _change_state(db, reservation_id, "REJECTED", at)
    return "CONFIRMED" if decision == "approve" else "REJECTED"
