"""NotificationService: which operations notify whom, delivery to the browser, and the
boundary rule that a failed notification never fails a reservation."""

from __future__ import annotations

from datetime import timedelta

import cinema.main
from test_c02_operations import client, create, decide, login, make_screening, pending, row_seats  # noqa: F401


def deliver(client, user_id):
    return client.post(f"/users/{user_id}/notifications/deliver").json()


def kinds(client, user_id):
    return [n["kind"] for n in deliver(client, user_id)]


def confirmed_reservation(client):
    user, screening = login(client), make_screening(timedelta(hours=3))
    rid = create(client, user, screening, row_seats()[:2]).json()["reservation_id"]
    assert client.post(f"/reservations/{rid}/confirm").json()["state"] == "CONFIRMED"
    return user, rid


def test_confirm_notifies_the_viewer_once(client):
    user, _ = confirmed_reservation(client)
    [n] = deliver(client, user)
    assert n["kind"] == "RESERVATION_CONFIRMED"
    assert "sedadla A1, A2" in n["body"]
    assert deliver(client, user) == []  # delivered, so it doesn't pop up again


def test_create_alone_does_not_notify(client):
    user, screening = login(client), make_screening(timedelta(hours=3))
    create(client, user, screening, row_seats()[:2])
    assert deliver(client, user) == []


def test_vip_seat_notifies_saved_then_approved(client):
    rid, _, _ = pending(client)
    user = login(client)  # same email as pending() used, so the same user
    assert kinds(client, user) == ["RESERVATION_PENDING_APPROVAL"]
    decide(client, rid, "approve")
    assert kinds(client, user) == ["RESERVATION_APPROVED"]


def test_rejection_is_notified(client):
    rid, _, _ = pending(client)
    user = login(client)
    deliver(client, user)
    decide(client, rid, "reject")
    assert kinds(client, user) == ["RESERVATION_REJECTED"]


def test_new_movie_notifies_every_user(client):
    a, b = login(client, "a@example.com"), login(client, "b@example.com")
    resp = client.post("/movies", json={"title": "Nový film", "duration_minutes": 100, "genres": ["Drama"]})
    assert resp.status_code == 201
    assert any(m["title"] == "Nový film" and m["genres"] == ["Drama"] for m in client.get("/movies").json())
    for user in (a, b):
        [n] = deliver(client, user)
        assert n["kind"] == "NEW_MOVIE" and "Nový film" in n["body"]


def test_failed_notification_never_fails_the_reservation(client, monkeypatch):
    class Broken:
        def __getattr__(self, name):
            def fail(*args):
                raise RuntimeError("provider down")
            return fail

    monkeypatch.setattr(cinema.main, "notifier", Broken())
    user, rid = confirmed_reservation(client)  # asserts the confirm itself still succeeds
    assert client.get(f"/reservations/{rid}").json()["state"] == "CONFIRMED"
    assert deliver(client, user) == []
