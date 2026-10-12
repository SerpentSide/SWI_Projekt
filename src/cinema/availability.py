"""Availability: the one definition of "occupied" (ADR-003, v0.2) and the no-orphan rule (BR-04, ADR-006).

Read-only. It owns the rules, not any data, and never changes a reservation."""

from __future__ import annotations

from collections import defaultdict

# The ONE place that defines "occupied": confirmed, or a draft whose hold is still running.
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


def occupied_seat_ids(db, screening_id: int, at: str) -> set[int]:
    return {row["seat_id"] for row in db.execute(OCCUPIED_SEATS_SQL, (screening_id, at, at))}


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


def orphaned_row(hall_seats: list[dict], occupied: set[int], requested: set[int]) -> str | None:
    """The first row the selection would leave with an isolated free seat, or None."""
    by_id = {s["id"]: s for s in hall_seats}
    rows = defaultdict(list)
    for seat in hall_seats:
        rows[seat["row_label"]].append(seat)
    for label in {by_id[i]["row_label"] for i in requested}:
        row = sorted(rows[label], key=lambda s: s["seat_number"])
        if has_isolated_free_seat([s["id"] in occupied or s["id"] in requested for s in row]):
            return label
    return None
