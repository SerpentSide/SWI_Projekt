"""C02 acceptance tests: one class per operation in docs/operations-specification.md.

Every test names the spec slice / verification example it checks. Unlike test_api.py
(which relies on random demo data) these build their own screenings, so a row is always
completely free and "now" relative to a screening is exact.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import cinema.main
from cinema.main import app, connect, reset_db
from cinema.seed import iso

def no_approval_seats() -> None:
    """The demo data has a VIP row needing approval; most tests want plain v0.1 seats."""
    conn = connect()
    try:
        conn.execute("UPDATE seats SET requires_approval = 0")
    finally:
        conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cinema.main, "DB_PATH", tmp_path / "cinema.sqlite3")
    reset_db()
    no_approval_seats()  # tests opt in to approval seats explicitly (v0.2)
    with TestClient(app) as c:
        yield c


def utc(delta: timedelta) -> str:
    return iso(datetime.now(UTC) + delta)


def make_screening(starts_in: timedelta, hall_id: int = 1) -> int:
    """A brand new screening (movie 1) that starts `starts_in` from now, with no reservations."""
    conn = connect()
    try:
        return conn.execute(
            "INSERT INTO screenings (movie_id, hall_id, starts_at, ends_at) VALUES (1, ?, ?, ?)",
            (hall_id, utc(starts_in), utc(starts_in + timedelta(hours=2))),
        ).lastrowid
    finally:
        conn.close()


def row_seats(hall_id: int = 1, row: int = 0) -> list[int]:
    """Seat ids of one row of a hall, left to right (row = index into the hall's rows)."""
    conn = connect()
    try:
        labels = [r["row_label"] for r in conn.execute(
            "SELECT DISTINCT row_label FROM seats WHERE hall_id = ? ORDER BY row_label", (hall_id,))]
        return [r["id"] for r in conn.execute(
            "SELECT id FROM seats WHERE hall_id = ? AND row_label = ? ORDER BY seat_number",
            (hall_id, labels[row]))]
    finally:
        conn.close()


def login(client, email="viewer@example.com") -> int:
    return client.post("/auth/login", json={"email": email}).json()["user_id"]


def create(client, user_id, screening_id, seat_ids):
    return client.post("/reservations", json={
        "user_id": user_id, "screening_id": screening_id, "seat_ids": seat_ids})


def plant(screening_id, user_id, seat_ids, state="DRAFT", hold_until=None) -> int:
    """Insert a reservation directly, bypassing the API - reaches states create() refuses."""
    conn = connect()
    try:
        rid = conn.execute(
            "INSERT INTO reservations (user_id, screening_id, state, hold_until) VALUES (?, ?, ?, ?)",
            (user_id, screening_id, state, hold_until or utc(timedelta(minutes=15))),
        ).lastrowid
        conn.executemany(
            "INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state) VALUES (?, ?, ?, ?)",
            [(rid, s, screening_id, state) for s in seat_ids])
        return rid
    finally:
        conn.close()


def stored(reservation_id):
    """(reservation.state, [reservation_seats.state]) exactly as stored, no lazy expiry."""
    conn = connect()
    try:
        r = conn.execute("SELECT state FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        seats = [x["state"] for x in conn.execute(
            "SELECT state FROM reservation_seats WHERE reservation_id = ?", (reservation_id,))]
        return r["state"], seats
    finally:
        conn.close()


def occupied_ids(client, screening_id) -> set[int]:
    seats = client.get(f"/screenings/{screening_id}/availability").json()["seats"]
    return {s["id"] for s in seats if s["occupied"]}


def race(fn_a, fn_b):
    """Run two callables at the same moment, return both results."""
    barrier, results = threading.Barrier(2), [None, None]

    def run(i, fn):
        barrier.wait(timeout=10)
        results[i] = fn()

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate((fn_a, fn_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


# --- OP-01 Create Reservation ------------------------------------------------


class TestCreate:
    def test_valid_request_makes_a_draft_with_a_15_minute_hold(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        seats = row_seats()[:2]
        resp = create(client, user, screening, seats)
        assert resp.status_code == 201
        hold_until = datetime.fromisoformat(resp.json()["hold_until"].replace("Z", "+00:00"))
        assert timedelta(minutes=14) < hold_until - datetime.now(UTC) <= timedelta(minutes=15)
        assert stored(resp.json()["reservation_id"]) == ("DRAFT", ["DRAFT", "DRAFT"])

    def test_screening_that_already_started_is_409(self, client):
        user, screening = login(client), make_screening(-timedelta(minutes=1))
        assert create(client, user, screening, row_seats()[:2]).status_code == 409

    def test_duplicate_seat_ids_are_422(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        seat = row_seats()[0]
        assert create(client, user, screening, [seat, seat]).status_code == 422

    def test_empty_seat_ids_are_422(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        assert create(client, user, screening, []).status_code == 422

    def test_seat_of_another_hall_is_404(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3), hall_id=1)
        assert create(client, user, screening, row_seats(hall_id=2)[:2]).status_code == 404

    def test_seat_held_as_live_draft_by_someone_else_is_409(self, client):
        alice, bob = login(client, "a@example.com"), login(client, "b@example.com")
        screening = make_screening(timedelta(hours=3))
        seats = row_seats()[:2]
        assert create(client, alice, screening, seats).status_code == 201
        assert create(client, bob, screening, seats).status_code == 409

    def test_seat_whose_hold_expired_can_be_taken_again(self, client):
        alice, bob = login(client, "a@example.com"), login(client, "b@example.com")
        screening = make_screening(timedelta(hours=3))
        seats = row_seats()[:2]
        plant(screening, alice, seats, hold_until=utc(-timedelta(seconds=1)))
        assert create(client, bob, screening, seats).status_code == 201

    def test_orphan_seat_is_422(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        assert create(client, user, screening, [row_seats()[1]]).status_code == 422

    def test_spec_example_a3_a4_ok_and_a4_a5_a6_orphan(self, client):
        """Spec OP-01 example on a hall row of 10: seats 1,2,9,10 occupied."""
        user, screening = login(client), make_screening(timedelta(hours=3))
        row = row_seats()
        plant(screening, user, [row[0], row[1], row[8], row[9]], state="CONFIRMED")
        assert create(client, user, screening, [row[3], row[4], row[5]]).status_code == 422
        assert create(client, user, screening, [row[2], row[3]]).status_code == 201

    def test_two_simultaneous_creates_on_one_seat_give_exactly_one_201(self, client):
        alice, bob = login(client, "a@example.com"), login(client, "b@example.com")
        for attempt in range(15):
            screening = make_screening(timedelta(hours=3 + attempt))
            seats = row_seats(row=attempt % 10)[:2]
            codes = race(
                lambda: create(TestClient(app), alice, screening, seats).status_code,
                lambda: create(TestClient(app), bob, screening, seats).status_code,
            )
            assert sorted(codes) == [201, 409], f"attempt {attempt}: {codes}"


# --- OP-02 Check Availability --------------------------------------------------


class TestAvailability:
    def test_fresh_screening_is_entirely_free(self, client):
        screening = make_screening(timedelta(hours=3))
        assert occupied_ids(client, screening) == set()

    def test_unknown_screening_is_404(self, client):
        assert client.get("/screenings/99999/availability").status_code == 404

    def test_confirmed_occupies_and_expired_draft_does_not(self, client):
        """Spec OP-02 example: A5 CONFIRMED -> occupied, A6 DRAFT with hold in the past -> free."""
        user, screening = login(client), make_screening(timedelta(hours=3))
        row = row_seats()
        plant(screening, user, [row[4]], state="CONFIRMED")
        plant(screening, user, [row[5]], hold_until=utc(-timedelta(seconds=1)))
        assert occupied_ids(client, screening) == {row[4]}

    def test_live_draft_occupies(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        seat = row_seats()[4]
        plant(screening, user, [seat], hold_until=utc(timedelta(minutes=5)))
        assert occupied_ids(client, screening) == {seat}

    def test_hold_boundary_is_exclusive(self, client, monkeypatch):
        """Spec OP-02 timing example: at exactly hold_until the seat is already free."""
        user, screening = login(client), make_screening(timedelta(hours=3))
        seat = row_seats()[4]
        hold = "2026-09-23T19:55:00Z"
        plant(screening, user, [seat], hold_until=hold)
        for now, expected in [
            ("2026-09-23T19:54:59Z", {seat}),   # one second before hold_until
            ("2026-09-23T19:55:00Z", set()),    # exactly hold_until
            ("2026-09-23T19:55:01Z", set()),    # one second after
        ]:
            monkeypatch.setattr(cinema.main, "now", lambda now=now: now)
            assert occupied_ids(client, screening) == expected, now

    def test_is_read_only(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = plant(screening, user, [row_seats()[4]], hold_until=utc(-timedelta(seconds=1)))
        before = stored(rid)
        for _ in range(3):
            client.get(f"/screenings/{screening}/availability")
        assert stored(rid) == before == ("DRAFT", ["DRAFT"])


# --- OP-03 Confirm Reservation -------------------------------------------------


class TestConfirm:
    def test_draft_becomes_confirmed_together_with_its_seats(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        assert client.post(f"/reservations/{rid}/confirm").status_code == 200
        assert stored(rid) == ("CONFIRMED", ["CONFIRMED", "CONFIRMED"])
        conn = connect()
        assert conn.execute("SELECT confirmed_at FROM reservations WHERE id = ?", (rid,)).fetchone()["confirmed_at"]
        conn.close()

    def test_unknown_reservation_is_404(self, client):
        assert client.post("/reservations/99999/confirm").status_code == 404

    def test_already_confirmed_is_409(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        client.post(f"/reservations/{rid}/confirm")
        assert client.post(f"/reservations/{rid}/confirm").status_code == 409

    def test_cancelled_is_409(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        client.post(f"/reservations/{rid}/cancel")
        assert client.post(f"/reservations/{rid}/confirm").status_code == 409

    def test_expired_hold_is_409(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = plant(screening, user, row_seats()[:2], hold_until=utc(-timedelta(seconds=1)))
        assert client.post(f"/reservations/{rid}/confirm").status_code == 409

    def test_expired_hold_reads_as_expired_and_holds_no_seats(self, client):
        """Observable outcome only - whether the row is rewritten is not specified (ADR-003)."""
        user, screening = login(client), make_screening(timedelta(hours=3))
        seats = row_seats()[:2]
        rid = plant(screening, user, seats, hold_until=utc(-timedelta(seconds=1)))
        assert client.post(f"/reservations/{rid}/confirm").status_code == 409
        assert client.get(f"/reservations/{rid}").json()["state"] == "EXPIRED"
        assert occupied_ids(client, screening) == set()

    def test_multi_seat_conflict_leaves_no_seat_confirmed(self, client):
        """Spec OP-03 atomicity example."""
        alice, bob = login(client, "a@example.com"), login(client, "b@example.com")
        screening = make_screening(timedelta(hours=3))
        seats = row_seats()[:2]
        mine = plant(screening, alice, seats)
        plant(screening, bob, [seats[1]], state="CONFIRMED")
        assert client.post(f"/reservations/{mine}/confirm").status_code == 409
        assert stored(mine) == ("DRAFT", ["DRAFT", "DRAFT"])

    def test_two_simultaneous_confirms_on_one_seat_give_exactly_one_200(self, client):
        """The C01 spike (Run 2) re-checked through the HTTP API."""
        alice, bob = login(client, "a@example.com"), login(client, "b@example.com")
        for attempt in range(15):
            screening = make_screening(timedelta(hours=3 + attempt))
            seats = row_seats(row=attempt % 10)[:2]
            first, second = plant(screening, alice, seats), plant(screening, bob, seats)
            codes = race(
                lambda: TestClient(app).post(f"/reservations/{first}/confirm").status_code,
                lambda: TestClient(app).post(f"/reservations/{second}/confirm").status_code,
            )
            assert sorted(codes) == [200, 409], f"attempt {attempt}: {codes}"


# --- OP-04 Cancel Reservation --------------------------------------------------


class TestCancel:
    def test_draft_before_start_is_cancelled_and_row_is_kept(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        assert client.post(f"/reservations/{rid}/cancel").status_code == 200
        assert stored(rid) == ("CANCELLED", ["CANCELLED", "CANCELLED"])
        assert client.get(f"/reservations/{rid}").status_code == 200  # cancel != delete
        assert occupied_ids(client, screening) == set()

    def test_confirmed_before_start_is_cancelled_and_seats_are_free_again(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        client.post(f"/reservations/{rid}/confirm")
        assert client.post(f"/reservations/{rid}/cancel").status_code == 200
        assert occupied_ids(client, screening) == set()

    def test_unknown_reservation_is_404(self, client):
        assert client.post("/reservations/99999/cancel").status_code == 404

    def test_already_cancelled_is_409(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        client.post(f"/reservations/{rid}/cancel")
        assert client.post(f"/reservations/{rid}/cancel").status_code == 409

    def test_expired_is_409(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = plant(screening, user, row_seats()[:2], hold_until=utc(-timedelta(seconds=1)))
        assert client.post(f"/reservations/{rid}/cancel").status_code == 409

    def test_confirmed_after_start_is_409(self, client):
        user, screening = login(client), make_screening(-timedelta(minutes=1))
        rid = plant(screening, user, row_seats()[:2], state="CONFIRMED")
        assert client.post(f"/reservations/{rid}/cancel").status_code == 409
        assert stored(rid)[0] == "CONFIRMED"

    def test_draft_after_start_is_409(self, client):
        """BR-03 applies to DRAFT and CONFIRMED alike."""
        user, screening = login(client), make_screening(-timedelta(minutes=1))
        rid = plant(screening, user, row_seats()[:2])
        assert client.post(f"/reservations/{rid}/cancel").status_code == 409
        assert stored(rid)[0] == "DRAFT"


# --- v0.2: seats that require approval (OP-03 variant, OP-05, OP-04, OP-02) ----------


def require_approval(seat_ids) -> None:
    conn = connect()
    try:
        conn.executemany("UPDATE seats SET requires_approval = 1 WHERE id = ?", [(s,) for s in seat_ids])
    finally:
        conn.close()


def decide(client, reservation_id, decision):
    return client.post(f"/reservations/{reservation_id}/decision", json={"decision": decision})


def pending(client, hours_ahead=3, row=0):
    """A PENDING_APPROVAL reservation reached the honest way: create, then confirm on a VIP seat."""
    user, screening = login(client), make_screening(timedelta(hours=hours_ahead))
    seats = row_seats(row=row)[:2]
    require_approval(seats)
    rid = create(client, user, screening, seats).json()["reservation_id"]
    assert client.post(f"/reservations/{rid}/confirm").json()["state"] == "PENDING_APPROVAL"
    return rid, screening, seats


class TestConfirmNeedingApproval:
    def test_confirm_on_a_vip_seat_waits_for_a_decision(self, client):
        rid, screening, seats = pending(client)
        assert stored(rid) == ("PENDING_APPROVAL", ["PENDING_APPROVAL", "PENDING_APPROVAL"])
        conn = connect()
        assert conn.execute("SELECT confirmed_at FROM reservations WHERE id = ?", (rid,)).fetchone()["confirmed_at"] is None
        conn.close()

    def test_one_vip_seat_among_ordinary_ones_is_enough(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        seats = row_seats()[:3]
        require_approval([seats[2]])
        rid = create(client, user, screening, seats).json()["reservation_id"]
        assert client.post(f"/reservations/{rid}/confirm").json()["state"] == "PENDING_APPROVAL"

    def test_ordinary_seats_still_confirm_straight_away(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        require_approval(row_seats(row=5))  # some other row needs approval, not this one
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        assert client.post(f"/reservations/{rid}/confirm").json()["state"] == "CONFIRMED"

    def test_confirm_again_on_a_pending_reservation_is_409(self, client):
        rid, *_ = pending(client)
        assert client.post(f"/reservations/{rid}/confirm").status_code == 409

    def test_pending_seats_stay_occupied_far_past_the_15_minute_hold(self, client):
        """Spec OP-05 delayed approval: the pending state is not on the hold clock."""
        rid, screening, seats = pending(client)
        conn = connect()
        conn.execute("UPDATE reservations SET hold_until = ? WHERE id = ?", (utc(-timedelta(hours=5)), rid))
        conn.close()
        assert occupied_ids(client, screening) == set(seats)
        assert client.get(f"/reservations/{rid}").json()["state"] == "PENDING_APPROVAL"

    def test_pending_seats_cannot_be_taken_by_someone_else(self, client):
        rid, screening, seats = pending(client)
        other = login(client, "other@example.com")
        assert create(client, other, screening, seats).status_code == 409

    def test_pending_reservation_of_a_started_screening_reads_as_expired_and_is_free(self, client):
        user, screening = login(client), make_screening(-timedelta(minutes=1))
        seats = row_seats()[:2]
        rid = plant(screening, user, seats, state="PENDING_APPROVAL")
        assert client.get(f"/reservations/{rid}").json()["state"] == "EXPIRED"
        assert occupied_ids(client, screening) == set()


class TestApprove:
    def test_approve_confirms_the_reservation_and_its_seats(self, client):
        rid, screening, seats = pending(client)
        resp = decide(client, rid, "approve")
        assert resp.status_code == 200 and resp.json()["state"] == "CONFIRMED"
        assert stored(rid) == ("CONFIRMED", ["CONFIRMED", "CONFIRMED"])
        conn = connect()
        assert conn.execute("SELECT confirmed_at FROM reservations WHERE id = ?", (rid,)).fetchone()["confirmed_at"]
        conn.close()
        assert occupied_ids(client, screening) == set(seats)

    def test_deciding_twice_is_409(self, client):
        rid, *_ = pending(client)
        assert decide(client, rid, "approve").status_code == 200
        assert decide(client, rid, "approve").status_code == 409

    def test_reject_frees_the_seats_and_keeps_the_row(self, client):
        rid, screening, seats = pending(client)
        resp = decide(client, rid, "reject")
        assert resp.status_code == 200 and resp.json()["state"] == "REJECTED"
        assert stored(rid) == ("REJECTED", ["REJECTED", "REJECTED"])
        assert occupied_ids(client, screening) == set()
        assert client.get(f"/reservations/{rid}").json()["state"] == "REJECTED"
        assert decide(client, rid, "approve").status_code == 409

    def test_unknown_reservation_is_404(self, client):
        assert decide(client, 99999, "approve").status_code == 404

    def test_decision_must_be_approve_or_reject(self, client):
        rid, *_ = pending(client)
        assert decide(client, rid, "maybe").status_code == 422
        assert stored(rid)[0] == "PENDING_APPROVAL"

    def test_a_draft_cannot_be_decided(self, client):
        user, screening = login(client), make_screening(timedelta(hours=3))
        rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
        assert decide(client, rid, "approve").status_code == 409
        assert stored(rid)[0] == "DRAFT"

    def test_approval_after_the_screening_started_is_409_and_reads_expired(self, client):
        user, screening = login(client), make_screening(-timedelta(minutes=1))
        rid = plant(screening, user, row_seats()[:2], state="PENDING_APPROVAL")
        assert decide(client, rid, "approve").status_code == 409
        assert stored(rid)[0] == "PENDING_APPROVAL"
        assert client.get(f"/reservations/{rid}").json()["state"] == "EXPIRED"

    def test_approval_that_collides_with_a_confirmed_seat_confirms_nothing(self, client):
        """The unique index stays the authority even if pending seats were somehow not blocked."""
        alice, bob = login(client, "a@example.com"), login(client, "b@example.com")
        screening = make_screening(timedelta(hours=3))
        seats = row_seats()[:2]
        mine = plant(screening, alice, seats, state="PENDING_APPROVAL")
        plant(screening, bob, [seats[1]], state="CONFIRMED")
        assert decide(client, mine, "approve").status_code == 409
        assert stored(mine) == ("PENDING_APPROVAL", ["PENDING_APPROVAL", "PENDING_APPROVAL"])

    def test_approve_and_cancel_arriving_together_always_end_cancelled(self, client):
        """Approve-then-cancel and cancel-then-approve are both legal orders; the end state is not."""
        for attempt in range(15):
            rid, screening, _ = pending(client, hours_ahead=3 + attempt, row=attempt % 10)
            approve_code, cancel_code = race(
                lambda: decide(TestClient(app), rid, "approve").status_code,
                lambda: TestClient(app).post(f"/reservations/{rid}/cancel").status_code,
            )
            assert cancel_code == 200, f"attempt {attempt}"
            assert approve_code in (200, 409), f"attempt {attempt}"
            assert stored(rid) == ("CANCELLED", ["CANCELLED", "CANCELLED"]), f"attempt {attempt}"
            assert occupied_ids(client, screening) == set()


class TestCancelPending:
    def test_pending_can_be_cancelled_and_seats_are_free_again(self, client):
        rid, screening, _ = pending(client)
        assert client.post(f"/reservations/{rid}/cancel").status_code == 200
        assert stored(rid) == ("CANCELLED", ["CANCELLED", "CANCELLED"])
        assert occupied_ids(client, screening) == set()
        assert decide(client, rid, "approve").status_code == 409

    def test_rejected_cannot_be_cancelled(self, client):
        rid, *_ = pending(client)
        decide(client, rid, "reject")
        assert client.post(f"/reservations/{rid}/cancel").status_code == 409
