"""C09 integration tests: CR-C08 "pending approval limit" through the real API and a real SQLite file.

The accepted Change Card is "C08 - CR-C08: Pending approval limit" in docs/intent-and-change.md; the test
ids, their expected results (written before the implementation) and the recorded runs are in
docs/evidence-and-evolution.md, "C09 - Integration, CI and Release Record".

Integration boundary: FastAPI TestClient -> cinema.main -> reservations -> SQLite file in tmp_path. Nothing is
mocked except the clock: `cinema.main.now` is replaced by a controllable instant, so 29:59 / 30:00 / 30:01
are exact. Every verdict on stored state is read back through a NEW connection (`committed`), which only sees
committed data - an HTTP status alone is not taken as proof.

TC-C08-01 and TC-C08-02 (the C08 regression basis) only use behaviour that existed before the change, so they
were also run against the before baseline (3b8f4bf); see the evidence file.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import cinema.db
import cinema.main
from cinema.db import connect, reset_db
from cinema.main import app
from cinema.seed import iso

T0 = datetime.fromisoformat("2030-01-01T17:00:00+00:00")  # create + confirm happen here
STARTS_AT = T0 + timedelta(hours=3)  # far enough that the start guard never decides these tests


class Clock:
    """The server's 'now' for the API under test (ISO-8601 UTC, as `cinema.db.now` returns it)."""

    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> str:
        return iso(self.at)

    def set(self, delta: timedelta) -> None:
        self.at = T0 + delta


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    c = Clock(T0)
    monkeypatch.setattr(cinema.main, "now", c)
    return c


@pytest.fixture()
def client(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(cinema.db, "DB_PATH", tmp_path / "cinema.sqlite3")
    reset_db()
    conn = connect()
    try:
        conn.execute("UPDATE seats SET requires_approval = 0")  # each test opts in to VIP seats itself
    finally:
        conn.close()
    with TestClient(app) as c:
        yield c


# --- fixture data ------------------------------------------------------------------------------------------


def screening() -> int:
    """R1: a screening of its own in hall 1, so no seeded reservation touches its seats."""
    conn = connect()
    try:
        return conn.execute(
            "INSERT INTO screenings (movie_id, hall_id, starts_at, ends_at) VALUES (1, 1, ?, ?)",
            (iso(STARTS_AT), iso(STARTS_AT + timedelta(hours=2))),
        ).lastrowid
    finally:
        conn.close()


def row(n: int = 0) -> list[int]:
    """Seat ids of the n-th row of hall 1, left to right."""
    conn = connect()
    try:
        labels = [r["row_label"] for r in conn.execute(
            "SELECT DISTINCT row_label FROM seats WHERE hall_id = 1 ORDER BY row_label")]
        return [r["id"] for r in conn.execute(
            "SELECT id FROM seats WHERE hall_id = 1 AND row_label = ? ORDER BY seat_number", (labels[n],))]
    finally:
        conn.close()


def needs_approval(seat_ids: list[int]) -> None:
    conn = connect()
    try:
        conn.executemany("UPDATE seats SET requires_approval = 1 WHERE id = ?", [(s,) for s in seat_ids])
    finally:
        conn.close()


def login(client, email: str) -> int:
    return client.post("/auth/login", json={"email": email}).json()["user_id"]


def operator(client) -> dict:
    return {"X-User-Id": str(login(client, "boxoffice@example.com"))}


def reserve(client, user: int, screening_id: int, seats: list[int]) -> tuple[int, dict]:
    """create + confirm through the API at the current clock; returns the id and the confirm response."""
    created = client.post("/reservations", json={"user_id": user, "screening_id": screening_id, "seat_ids": seats})
    assert created.status_code == 201, created.text
    rid = created.json()["reservation_id"]
    confirmed = client.post(f"/reservations/{rid}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    return rid, confirmed.json()


def pending(client, seats: list[int], email: str = "viewer@example.com") -> int:
    """A PENDING_APPROVAL reservation reached the honest way at T0: create, then confirm on VIP seats."""
    needs_approval(seats)
    rid, body = reserve(client, login(client, email), screening(), seats)
    assert body["state"] == "PENDING_APPROVAL"
    return rid


def decide(client, rid: int, decision: str = "approve"):
    return client.post(f"/reservations/{rid}/decision", json={"decision": decision}, headers=operator(client))


def committed(rid: int) -> tuple[str, str | None, list[str]]:
    """(reservations.state, confirmed_at, [reservation_seats.state]) through a NEW connection: committed data only."""
    conn = connect()
    try:
        r = conn.execute("SELECT state, confirmed_at FROM reservations WHERE id = ?", (rid,)).fetchone()
        seats = [x["state"] for x in conn.execute(
            "SELECT state FROM reservation_seats WHERE reservation_id = ? ORDER BY seat_id", (rid,))]
        return r["state"], r["confirmed_at"], seats
    finally:
        conn.close()


def double_confirmed_seats() -> list[tuple]:
    """BR-02 oracle, independent of the index: (screening, seat) pairs held by more than one CONFIRMED row."""
    conn = connect()
    try:
        return [tuple(r.values()) for r in conn.execute(
            """SELECT screening_id, seat_id, count(*) AS n FROM reservation_seats
               WHERE state = 'CONFIRMED' GROUP BY screening_id, seat_id HAVING n > 1""")]
    finally:
        conn.close()


def occupied(client, screening_id: int) -> set[int]:
    return {s["id"] for s in client.get(f"/screenings/{screening_id}/availability").json()["seats"] if s["occupied"]}


# --- C08 regression basis (also run against the before baseline) -------------------------------------------


def test_tc_c08_01_approve_next_to_a_confirmed_seat_confirms_both(client, clock):
    """TC-C08-01: B1 CONFIRMED on seat 1, B2 PENDING on seat 2 of the same row (adjacent, no shared seat);
    the box office approves B2 within the limit -> both CONFIRMED, no seat confirmed twice."""
    seats = row(0)
    sc = screening()
    b1, body = reserve(client, login(client, "b1@example.com"), sc, seats[0:2])
    assert body["state"] == "CONFIRMED"
    needs_approval(seats[2:4])
    b2, body = reserve(client, login(client, "b2@example.com"), sc, seats[2:4])
    assert body["state"] == "PENDING_APPROVAL"
    assert committed(b2) == ("PENDING_APPROVAL", None, ["PENDING_APPROVAL", "PENDING_APPROVAL"])

    clock.set(timedelta(minutes=10))
    resp = decide(client, b2)

    assert resp.status_code == 200 and resp.json()["state"] == "CONFIRMED"
    assert committed(b1)[0] == "CONFIRMED"
    assert committed(b2) == ("CONFIRMED", iso(T0 + timedelta(minutes=10)), ["CONFIRMED", "CONFIRMED"])
    assert double_confirmed_seats() == []


def test_tc_c08_02_approve_onto_an_already_confirmed_seat_is_409_and_writes_nothing(client, clock):
    """TC-C08-02: B2 PENDING on seats X, Y; then X is CONFIRMED by B1 (planted - create() would refuse it while
    B2 holds X); approve(B2) -> 409, B1 unchanged, B2 not CONFIRMED on any seat."""
    seats = row(0)
    sc = screening()
    needs_approval(seats[0:2])
    b2, _ = reserve(client, login(client, "b2@example.com"), sc, seats[0:2])
    conn = connect()
    try:  # B1: a CONFIRMED reservation on seat X, written directly (test setup, not a lifecycle write)
        b1 = conn.execute(
            "INSERT INTO reservations (user_id, screening_id, state, hold_until, confirmed_at)"
            " VALUES (1, ?, 'CONFIRMED', ?, ?)", (sc, iso(T0), iso(T0))).lastrowid
        conn.execute("INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state)"
                     " VALUES (?, ?, ?, 'CONFIRMED')", (b1, seats[0], sc))
    finally:
        conn.close()

    clock.set(timedelta(minutes=10))
    resp = decide(client, b2)

    assert resp.status_code == 409
    assert committed(b1) == ("CONFIRMED", iso(T0), ["CONFIRMED"])
    assert committed(b2) == ("PENDING_APPROVAL", None, ["PENDING_APPROVAL", "PENDING_APPROVAL"])
    assert double_confirmed_seats() == []


# --- new behaviour: AC-01 .. AC-05 of CR-C08 ---------------------------------------------------------------

LIMIT = timedelta(minutes=30)


def test_tc_c09_01_confirm_records_the_approval_deadline(client, clock):
    """AC-05: entering PENDING_APPROVAL at T0 stores pending_until = T0 + 30:00 (server clock, UTC)."""
    rid = pending(client, row(0)[0:2])

    assert client.get(f"/reservations/{rid}").json()["pending_until"] == iso(T0 + LIMIT)
    conn = connect()
    try:
        assert conn.execute("SELECT pending_until FROM reservations WHERE id = ?", (rid,)).fetchone() == {
            "pending_until": iso(T0 + LIMIT)}
    finally:
        conn.close()


def test_tc_c09_02_approve_at_29_59_confirms(client, clock):
    """AC-01: approve at age 29:59 -> 200 CONFIRMED, and the committed state is CONFIRMED on both tables."""
    rid = pending(client, row(0)[0:2])

    clock.set(LIMIT - timedelta(seconds=1))
    resp = decide(client, rid)

    assert resp.status_code == 200 and resp.json()["state"] == "CONFIRMED"
    assert committed(rid) == ("CONFIRMED", iso(T0 + LIMIT - timedelta(seconds=1)), ["CONFIRMED", "CONFIRMED"])


@pytest.mark.parametrize("age", [LIMIT, LIMIT + timedelta(seconds=1)], ids=["30:00", "30:01"])
def test_tc_c09_03_approve_at_or_after_the_limit_is_409_and_confirms_nothing(client, clock, age):
    """AC-03 (exactly 30:00 is already late) and AC-02 (30:01): 409 naming EXPIRED; no CONFIRMED row is written;
    the reservation reads EXPIRED and its seats are free again."""
    seats = row(0)[0:2]
    rid = pending(client, seats)
    sc = client.get(f"/reservations/{rid}").json()["screening_id"]

    clock.set(age)
    resp = decide(client, rid)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Cannot approve a EXPIRED reservation"
    assert committed(rid) == ("PENDING_APPROVAL", None, ["PENDING_APPROVAL", "PENDING_APPROVAL"])  # never stored
    assert client.get(f"/reservations/{rid}").json()["state"] == "EXPIRED"
    assert occupied(client, sc).isdisjoint(seats)
    assert double_confirmed_seats() == []


def test_tc_c09_04_reject_and_cancel_after_the_limit_are_409(client, clock):
    """AC-02 consequence: an expired pending request is terminal, like an expired DRAFT (BR-03)."""
    rid = pending(client, row(0)[0:2])

    clock.set(LIMIT)
    assert decide(client, rid, "reject").status_code == 409
    assert client.post(f"/reservations/{rid}/cancel").status_code == 409
    assert committed(rid)[0] == "PENDING_APPROVAL"


def test_tc_c09_05_pending_still_outlives_the_15_minute_draft_hold(client, clock):
    """Regression of the v0.2 claim 'pending is not on the hold clock': at 20:00 (after the DRAFT hold, before
    the limit) the seats are still occupied and the request can be approved."""
    seats = row(0)[0:2]
    rid = pending(client, seats)
    sc = client.get(f"/reservations/{rid}").json()["screening_id"]

    clock.set(timedelta(minutes=20))
    assert client.get(f"/reservations/{rid}").json()["state"] == "PENDING_APPROVAL"
    assert set(seats) <= occupied(client, sc)
    assert decide(client, rid).status_code == 200


def test_tc_c09_06_after_expiry_the_seat_can_be_sold_once_and_the_old_request_never_confirms(client, clock):
    """AC-04 (BR-02 regression): after the limit another viewer takes and confirms the same seats; a late approve
    of the expired request is still 409 and no seat is ever CONFIRMED twice."""
    seats = row(0)[0:2]
    old = pending(client, seats, "first@example.com")
    sc = client.get(f"/reservations/{old}").json()["screening_id"]
    conn = connect()
    try:
        conn.execute("UPDATE seats SET requires_approval = 0")  # the newcomer confirms straight away
    finally:
        conn.close()

    clock.set(LIMIT + timedelta(minutes=1))
    new, body = reserve(client, login(client, "second@example.com"), sc, seats)
    assert body["state"] == "CONFIRMED"
    assert decide(client, old).status_code == 409

    assert committed(new)[0] == "CONFIRMED"
    assert committed(old)[0] == "PENDING_APPROVAL"
    assert double_confirmed_seats() == []


# --- release / data: an existing database from before the change ------------------------------------------


def test_tc_c09_07_upgrade_adds_the_column_and_expires_legacy_pending_rows(tmp_path, monkeypatch, clock):
    """Migration: a database created before CR-C08 has no `pending_until`. `add_pending_until` adds it and is
    idempotent; a legacy PENDING_APPROVAL row (NULL deadline) reads EXPIRED and cannot be approved (fail-closed,
    as decided in the Change Card)."""
    path = tmp_path / "old.sqlite3"
    monkeypatch.setattr(cinema.db, "DB_PATH", path)
    reset_db()
    conn = connect()
    try:  # turn it into a before-change database: drop the column, plant one legacy pending request
        conn.execute("ALTER TABLE reservations DROP COLUMN pending_until")
        sc = conn.execute(
            "INSERT INTO screenings (movie_id, hall_id, starts_at, ends_at) VALUES (1, 1, ?, ?)",
            (iso(STARTS_AT), iso(STARTS_AT + timedelta(hours=2)))).lastrowid
        seat = conn.execute("SELECT id FROM seats WHERE hall_id = 1 ORDER BY id LIMIT 1").fetchone()["id"]
        rid = conn.execute("INSERT INTO reservations (user_id, screening_id, state, hold_until)"
                           " VALUES (1, ?, 'PENDING_APPROVAL', ?)", (sc, iso(T0))).lastrowid
        conn.execute("INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state)"
                     " VALUES (?, ?, ?, 'PENDING_APPROVAL')", (rid, seat, sc))
    finally:
        conn.close()

    with TestClient(app) as client:  # the app's startup runs the upgrade
        assert cinema.db.add_pending_until() is False  # already done by startup: idempotent
        assert client.get(f"/reservations/{rid}").json()["state"] == "EXPIRED"
        assert decide(client, rid).status_code == 409
    assert committed(rid)[0] == "PENDING_APPROVAL"
