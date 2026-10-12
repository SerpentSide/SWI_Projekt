"""Reservation Store access: connection, write transaction, schema + demo data loading.

SQLite is the only place that persists data. `uq_confirmed_seat_per_screening` in schema.sql is the
last line of defence for BR-02 (ADR-004)."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cinema.seed import iso, seed_random

HERE = Path(__file__).resolve().parent
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
