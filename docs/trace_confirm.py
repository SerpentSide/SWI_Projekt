"""Runtime trace of the Confirm conflict branch (C03 part A evidence).

Run from the repository root:  python -X utf8 docs/trace_confirm.py
Uses a throw-away database and touches nothing else.
"""
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cinema.main as m
from cinema.seed import iso
from fastapi.testclient import TestClient

m.DB_PATH = Path(tempfile.mkdtemp()) / "trace.sqlite3"
m.reset_db()
db = m.connect()
db.execute("UPDATE seats SET requires_approval = 0")
future = lambda h: iso(datetime.now(UTC) + timedelta(hours=h))
scr = db.execute(
    "INSERT INTO screenings (movie_id, hall_id, starts_at, ends_at) VALUES (1, 1, ?, ?)",
    (future(3), future(5)),
).lastrowid
seats = [r["id"] for r in db.execute("SELECT id FROM seats WHERE hall_id = 1 ORDER BY row_label, seat_number LIMIT 2")]
db.execute("INSERT OR IGNORE INTO users (email) VALUES ('a@x.cz'), ('b@x.cz')")
uid = [r["id"] for r in db.execute("SELECT id FROM users WHERE email IN ('a@x.cz','b@x.cz') ORDER BY email")]


def plant(user):
    rid = db.execute(
        "INSERT INTO reservations (user_id, screening_id, state, hold_until) VALUES (?, ?, 'DRAFT', ?)",
        (user, scr, future(1)),
    ).lastrowid
    db.executemany(
        "INSERT INTO reservation_seats (reservation_id, seat_id, screening_id, state) VALUES (?, ?, ?, 'DRAFT')",
        [(rid, s, scr) for s in seats],
    )
    return rid


first, second = plant(uid[0]), plant(uid[1])
client = TestClient(m.app)

r1 = client.post(f"/reservations/{first}/confirm")
print("first  confirm ->", r1.status_code, r1.json())
r2 = client.post(f"/reservations/{second}/confirm")
print("second confirm ->", r2.status_code, r2.json())
stored = lambda rid: (
    db.execute("SELECT state FROM reservations WHERE id=?", (rid,)).fetchone()["state"],
    [x["state"] for x in db.execute("SELECT state FROM reservation_seats WHERE reservation_id=?", (rid,))],
)
print("stored first  :", stored(first))
print("stored second :", stored(second), " (must be untouched DRAFT)")

# Where exactly does SQLite raise? Replay set_state() by hand for the loser, statement by statement.
db2 = m.connect()
db2.execute("BEGIN IMMEDIATE")
db2.execute("UPDATE reservations SET state = 'CONFIRMED' WHERE id = ?", (second,))
print("UPDATE reservations       : ok (no error)")
try:
    db2.execute("UPDATE reservation_seats SET state = 'CONFIRMED' WHERE reservation_id = ?", (second,))
    print("UPDATE reservation_seats  : ok (no error)")
except sqlite3.IntegrityError as exc:
    print("UPDATE reservation_seats  : raised", type(exc).__name__, "-", exc)
try:
    db2.execute("COMMIT")
    print("COMMIT                    : reached (a failed statement does not abort the transaction)")
except sqlite3.Error as exc:
    print("COMMIT                    : raised", exc)
db2.close()
