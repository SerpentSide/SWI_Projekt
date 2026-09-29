"""CP1 walking skeleton: create -> read -> availability, plus the error paths.

Each test gets a fresh SQLite file in pytest's tmp_path. The demo data is random, so
every test looks up a future screening with a completely free row instead of relying
on fixed ids.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import cinema.main
from cinema.main import app, connect, has_isolated_free_seat, reset_db

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


def free_row(client):
    """(screening_id, {seat_number: seat_id}) for a future screening's fully free row."""
    now = datetime.now().astimezone()
    for screening in client.get("/screenings").json():
        if datetime.fromisoformat(screening["starts_at"]) <= now:
            continue
        seats = client.get(f"/screenings/{screening['id']}/availability").json()["seats"]
        rows = {}
        for seat in seats:
            rows.setdefault(seat["row_label"], []).append(seat)
        for row in rows.values():
            if not any(s["occupied"] for s in row):
                return screening["id"], {s["seat_number"]: s["id"] for s in row}
    pytest.fail("no future screening with a free row in the demo data")


def occupied(client, screening_id):
    seats = client.get(f"/screenings/{screening_id}/availability").json()["seats"]
    return {s["id"] for s in seats if s["occupied"]}


def test_walking_skeleton(client):
    user_id = client.post("/auth/login", json={"email": "a@example.com"}).json()["user_id"]
    screening_id, row = free_row(client)
    seats = [row[1], row[2]]
    before = occupied(client, screening_id)

    resp = client.post("/reservations", json={"user_id": user_id, "screening_id": screening_id, "seat_ids": seats})
    assert resp.status_code == 201
    reservation_id = resp.json()["reservation_id"]

    assert client.get(f"/reservations/{reservation_id}").json()["state"] == "DRAFT"
    assert occupied(client, screening_id) == before | set(seats)

    assert client.post(f"/reservations/{reservation_id}/confirm").json()["state"] == "CONFIRMED"
    assert client.post(f"/reservations/{reservation_id}/cancel").json()["state"] == "CANCELLED"


def test_taken_seat_is_409(client):
    screening_id, row = free_row(client)
    body = {"user_id": 1, "screening_id": screening_id, "seat_ids": [row[1], row[2]]}
    assert client.post("/reservations", json=body).status_code == 201
    assert client.post("/reservations", json=body).status_code == 409


def test_orphan_seat_is_422(client):
    # Seat 2 of an empty row strands seat 1 (ADR-006).
    screening_id, row = free_row(client)
    resp = client.post("/reservations", json={"user_id": 1, "screening_id": screening_id, "seat_ids": [row[2]]})
    assert resp.status_code == 422


def test_unknown_screening_or_seat_is_404(client):
    screening_id, _ = free_row(client)
    assert client.post("/reservations", json={"user_id": 1, "screening_id": 99999, "seat_ids": [1]}).status_code == 404
    assert client.post("/reservations", json={"user_id": 1, "screening_id": screening_id, "seat_ids": [99999]}).status_code == 404


def test_seed_has_no_hall_overlaps_or_orphans(client):
    conn = connect()
    overlaps = conn.execute(
        """SELECT count(*) AS n FROM screenings a JOIN screenings b
           ON a.hall_id = b.hall_id AND a.id < b.id
          AND a.starts_at < b.ends_at AND b.starts_at < a.ends_at"""
    ).fetchone()["n"]
    conn.close()
    assert overlaps == 0

    for screening in client.get("/screenings").json():
        rows = {}
        for seat in client.get(f"/screenings/{screening['id']}/availability").json()["seats"]:
            rows.setdefault(seat["row_label"], []).append(seat["occupied"])
        assert not any(has_isolated_free_seat(row) for row in rows.values())


def test_double_confirm_is_409(client):
    """Two DRAFT holds on the same seat are legal; only one can be confirmed (ADR-004)."""
    screening_id, row = free_row(client)
    seats = [row[1], row[2]]
    body = {"user_id": 1, "screening_id": screening_id, "seat_ids": seats}
    first = client.post("/reservations", json=body).json()["reservation_id"]
    # A second hold on the same seats is refused by the availability check, so plant it
    # directly - this is the state two racing viewers can reach.
    conn = connect()
    second = conn.execute(
        "INSERT INTO reservations (user_id, screening_id, state, hold_until) VALUES (2, ?, 'DRAFT', '2999-01-01T00:00:00Z')",
        (screening_id,),
    ).lastrowid
    conn.executemany(
        "INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state) VALUES (?, ?, ?, 'DRAFT')",
        [(second, seat, screening_id) for seat in seats],
    )
    conn.close()

    assert client.post(f"/reservations/{first}/confirm").status_code == 200
    assert client.post(f"/reservations/{second}/confirm").status_code == 409
    assert client.get(f"/reservations/{second}").json()["state"] == "DRAFT"
