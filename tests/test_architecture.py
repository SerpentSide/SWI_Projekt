"""Architecture conformance (C03 L2, ADR-008).

Rule: only Reservation Management (src/cinema/reservations.py) may change a Reservation's lifecycle
state, i.e. write to `reservations` or `reservation_seats`. `seed.py` is the one exception: it loads
demo data and is not part of the lifecycle. `schema.sql` / `seed.sql` are not Python code.

The check scans the source of every module in src/cinema for SQL that writes to those tables. It is a
text scan, so it is deliberately simple; `test_the_check_detects_a_violation` proves that it can fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import cinema.db
from cinema import reservations
from cinema.lifecycle import Transition

SRC = Path(__file__).resolve().parents[1] / "src" / "cinema"
OWNER = "reservations.py"
NOT_LIFECYCLE = {"seed.py"}  # demo data loader

WRITES_RESERVATION_STATE = re.compile(
    r"\b(UPDATE\s+reservations?(_seats)?|INSERT\s+INTO\s+reservations?(_seats)?|DELETE\s+FROM\s+reservations?(_seats)?)\b",
    re.IGNORECASE,
)


def violations(sources: dict[str, str]) -> list[str]:
    """Modules other than the owner (and the demo loader) that write to the reservation tables."""
    return [
        name
        for name, text in sorted(sources.items())
        if name not in {OWNER, *NOT_LIFECYCLE} and WRITES_RESERVATION_STATE.search(text)
    ]


def sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in SRC.glob("*.py")}


def test_only_reservation_management_changes_reservation_state():
    assert violations(sources()) == []


def test_the_check_detects_a_violation():
    """A copy of the code with a stray write in the HTTP module must fail the rule."""
    bad = {**sources(), "main.py": 'db.execute("UPDATE reservations SET state = ? WHERE id = ?")'}
    assert violations(bad) == ["main.py"]
    assert violations({**sources(), "x.py": "INSERT INTO reservation_seats VALUES (1)"}) == ["x.py"]


def test_reservation_management_does_not_depend_on_http_or_notifications():
    """D4 / G2: no arrow from Reservation Management to the HTTP layer or the Notification Integration."""
    text = (SRC / OWNER).read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:from|import)\s+([\w.]+)", text, re.MULTILINE))
    assert not {m for m in imported if m.startswith(("fastapi", "starlette", "cinema.main", "cinema.notifications"))}


def test_a_state_change_outside_a_transaction_is_refused(tmp_path, monkeypatch):
    """Part A8: a forgotten write_tx must fail loudly instead of leaving the two tables apart."""
    monkeypatch.setattr(cinema.db, "DB_PATH", tmp_path / "cinema.sqlite3")
    cinema.db.reset_db()
    db = cinema.db.connect()
    try:
        rid = db.execute("SELECT id FROM reservations LIMIT 1").fetchone()["id"]
        with pytest.raises(RuntimeError, match="write_tx"):
            reservations._change_state(db, rid, Transition("CANCELLED", "cancelled_at"), "2026-01-01T00:00:00Z")
        assert db.execute("SELECT state FROM reservations WHERE id = ?", (rid,)).fetchone()["state"] == "CONFIRMED"
    finally:
        db.close()
