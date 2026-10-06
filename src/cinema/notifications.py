"""NotificationService -- the outbound boundary from the Project Frame.

The interface is what the operations call (OP-03 step 5, OP-05). The one implementation
today, `BrowserNotifications`, writes each message to the `notifications` table; the
frontend polls for undelivered rows and shows them as browser notifications. A real
e-mail/SMS provider would be another implementation of the same interface.

Callers go through `cinema.main.notify`, which runs after the business transaction has
committed and swallows every error: a failed notification never fails a reservation.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Protocol

from cinema.seed import TZ


class NotificationService(Protocol):
    def reservation_confirmed(self, db: sqlite3.Connection, reservation_id: int) -> None: ...
    def reservation_pending_approval(self, db: sqlite3.Connection, reservation_id: int) -> None: ...
    def reservation_approved(self, db: sqlite3.Connection, reservation_id: int) -> None: ...
    def reservation_rejected(self, db: sqlite3.Connection, reservation_id: int) -> None: ...
    def new_movie(self, db: sqlite3.Connection, movie_id: int) -> None: ...


class BrowserNotifications:
    """Stores messages for the browser to pick up (POST /users/{id}/notifications/deliver)."""

    def reservation_confirmed(self, db, reservation_id):
        self._about_reservation(db, reservation_id, "RESERVATION_CONFIRMED", "Rezervace potvrzena")

    def reservation_pending_approval(self, db, reservation_id):
        self._about_reservation(
            db, reservation_id, "RESERVATION_PENDING_APPROVAL", "Rezervace uložena - čeká na schválení"
        )

    def reservation_approved(self, db, reservation_id):
        self._about_reservation(db, reservation_id, "RESERVATION_APPROVED", "Rezervace schválena")

    def reservation_rejected(self, db, reservation_id):
        self._about_reservation(db, reservation_id, "RESERVATION_REJECTED", "Rezervace zamítnuta")

    def new_movie(self, db, movie_id):
        title = db.execute("SELECT title FROM movies WHERE id = ?", (movie_id,)).fetchone()["title"]
        # No genre subscriptions yet: every user gets it.
        db.execute(
            """INSERT INTO notifications (user_id, kind, title, body)
               SELECT id, 'NEW_MOVIE', 'Nový film v programu', ? FROM users""",
            (f"{title} - podívej se na promítání a rezervuj si místo.",),
        )

    def _about_reservation(self, db, reservation_id, kind, title):
        r = db.execute(
            """SELECT r.user_id, m.title, s.starts_at, h.name AS hall
               FROM reservations r
               JOIN screenings s ON s.id = r.screening_id
               JOIN movies m ON m.id = s.movie_id
               JOIN halls h ON h.id = s.hall_id
               WHERE r.id = ?""",
            (reservation_id,),
        ).fetchone()
        seats = ", ".join(
            f"{row['row_label']}{row['seat_number']}"
            for row in db.execute(
                """SELECT st.row_label, st.seat_number
                   FROM reservation_seats rs JOIN seats st ON st.id = rs.seat_id
                   WHERE rs.reservation_id = ? ORDER BY st.row_label, st.seat_number""",
                (reservation_id,),
            )
        )
        starts = datetime.fromisoformat(r["starts_at"]).astimezone(TZ)
        body = f"{r['title']}, {starts.day}. {starts.month}. {starts:%H:%M}, {r['hall']}, sedadla {seats}"
        db.execute(
            "INSERT INTO notifications (user_id, kind, title, body) VALUES (?, ?, ?, ?)",
            (r["user_id"], kind, title, body),
        )
