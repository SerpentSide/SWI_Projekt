-- Cinema seat reservation -- schema as far as CP1 needs it.
--
-- The only non-obvious part is the partial unique index at the bottom.
-- See docs/architecture-and-decisions.md, ADR-004 and ADR-005.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS reservation_seats;
DROP TABLE IF EXISTS reservations;
DROP TABLE IF EXISTS screenings;
DROP TABLE IF EXISTS seats;
DROP TABLE IF EXISTS halls;
DROP TABLE IF EXISTS movies;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id    INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE movies (
    id               INTEGER PRIMARY KEY,
    title            TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0)
);

CREATE TABLE halls (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- The reserved resource.
CREATE TABLE seats (
    id          INTEGER PRIMARY KEY,
    hall_id     INTEGER NOT NULL REFERENCES halls (id),
    row_label   TEXT    NOT NULL,
    seat_number INTEGER NOT NULL,
    UNIQUE (hall_id, row_label, seat_number)
);

CREATE TABLE screenings (
    id         INTEGER PRIMARY KEY,
    movie_id   INTEGER NOT NULL REFERENCES movies (id),
    hall_id    INTEGER NOT NULL REFERENCES halls (id),
    starts_at  TEXT    NOT NULL,  -- ISO-8601 UTC, e.g. 2026-09-15T18:00:00Z
    ends_at    TEXT    NOT NULL,
    CHECK (ends_at > starts_at)
);

CREATE TABLE reservations (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users (id),
    screening_id INTEGER NOT NULL REFERENCES screenings (id),
    state        TEXT    NOT NULL DEFAULT 'DRAFT'
                 CHECK (state IN ('DRAFT', 'CONFIRMED', 'CANCELLED', 'EXPIRED')),
    hold_until   TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    confirmed_at TEXT,
    cancelled_at TEXT
);

-- One row per seat held by a reservation. A reservation may hold several seats.
--
-- screening_id and state are denormalised copies of the parent reservation's columns.
-- They exist for exactly one reason: SQLite (like PostgreSQL) cannot build an index
-- across two tables, and we want the no-double-booking invariant to live in the
-- database rather than in application code. The cost is that both columns must be
-- written whenever the parent reservation changes state. ADR-005 records that trade.
CREATE TABLE reservation_seats (
    reservation_id INTEGER NOT NULL REFERENCES reservations (id) ON DELETE CASCADE,
    seat_id        INTEGER NOT NULL REFERENCES seats (id),
    screening_id   INTEGER NOT NULL REFERENCES screenings (id),
    state          TEXT    NOT NULL
                   CHECK (state IN ('DRAFT', 'CONFIRMED', 'CANCELLED', 'EXPIRED')),
    PRIMARY KEY (reservation_id, seat_id)
);

-- THE invariant: a seat can be confirmed at most once per screening.
--
-- Partial, so that DRAFT / CANCELLED / EXPIRED rows may coexist freely for the same
-- seat -- several people are allowed to *hold* a seat's candidacy, but only one of
-- them can end up owning it.
--
-- This is equivalent to the time-based rule in the Project Frame only while screenings
-- in one hall never overlap. That assumption is recorded in
-- docs/intent-and-change.md and in ADR-004.
CREATE UNIQUE INDEX uq_confirmed_seat_per_screening
    ON reservation_seats (screening_id, seat_id)
    WHERE state = 'CONFIRMED';

-- Supporting index for the availability query.
CREATE INDEX ix_reservation_seats_screening ON reservation_seats (screening_id, seat_id);
