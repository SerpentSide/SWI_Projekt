"""Reservation Store access: connection, write transaction, schema + demo data loading.

SQLite is the only place that persists data. `uq_confirmed_seat_per_screening` in schema.sql is the
last line of defence for BR-02 (ADR-004)."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cinema.seed import TZ, iso, seed_random

HERE = Path(__file__).resolve().parent
TOP_UP_AHEAD = timedelta(days=7)  # a demo database with less schedule than this left gets more
STAFF = {"boxoffice@example.com": "BOX_OFFICE", "admin@example.com": "ADMIN"}  # as in seed.sql
DB_PATH = Path(os.environ.get("CINEMA_DB", HERE.parents[1] / "cinema.sqlite3"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Autocommit connection; writes open their own transaction via `write_tx`."""
    conn = sqlite3.connect(path or DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = lambda cursor, row: {col[0]: value for col, value in zip(cursor.description, row)}
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def write_tx(db: sqlite3.Connection):
    """BEGIN IMMEDIATE takes SQLite's single write lock up front, so the checks and the
    writes inside one request can't interleave with another writer (ADR-002, ADR-004)."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def now() -> str:
    return iso(datetime.now(UTC))


def reset_db(path: Path | None = None) -> None:
    """Drop everything and load schema + demo data (random screenings and reservations)."""
    conn = connect(path)
    conn.row_factory = None
    try:
        conn.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
        conn.executescript((HERE / "seed.sql").read_text(encoding="utf-8"))
        with write_tx(conn):
            seed_random(conn)
    finally:
        conn.close()


def add_roles(path: Path | None = None) -> bool:
    """Bring a database created before roles existed up to date: the `users.role` column and the staff
    accounts of seed.sql (an existing user with a staff e-mail gets the staff role). Returns whether it did."""
    conn = connect(path)
    try:
        if any(col["name"] == "role" for col in conn.execute("PRAGMA table_info(users)")):
            return False
        with write_tx(conn):
            conn.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'VIEWER'"
                " CHECK (role IN ('VIEWER', 'BOX_OFFICE', 'ADMIN'))"
            )
            for email, role in STAFF.items():
                conn.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
                conn.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
        return True
    finally:
        conn.close()


def top_up_screenings(path: Path | None = None) -> int:
    """Keep a long-lived demo database usable without wiping it.

    The schedule is generated once, up to the end of the following month, so an old database runs out of
    future screenings. When the latest one starts less than TOP_UP_AHEAD from now, new screenings are added
    from the day after it (never earlier than today) to the end of next month. Starting on a day with no
    existing screenings keeps halls free of overlaps (ADR-004's assumption). Returns how many were added."""
    conn = connect(path)
    conn.row_factory = None
    try:
        with write_tx(conn):
            (latest,) = conn.execute("SELECT max(starts_at) FROM screenings").fetchone()
            if latest is not None and latest > iso(datetime.now(UTC) + TOP_UP_AHEAD):
                return 0
            first_day = date.today()
            if latest is not None:
                day_after = datetime.fromisoformat(latest.replace("Z", "+00:00")).astimezone(TZ).date() + timedelta(days=1)
                first_day = max(first_day, day_after)
            (before,) = conn.execute("SELECT count(*) FROM screenings").fetchone()
            seed_random(conn, first_day=first_day)
            (after,) = conn.execute("SELECT count(*) FROM screenings").fetchone()
        return after - before
    finally:
        conn.close()
