-- Cinema seat reservation -- schema as far as CP1 needs it.
--
-- The only non-obvious part is the partial unique index at the bottom.
-- See docs/architecture-and-decisions.md, ADR-004 and ADR-005.

DROP TABLE IF EXISTS reservation_seats, reservations, screenings, seats, halls, movies, users CASCADE;
DROP TYPE IF EXISTS reservation_state CASCADE;

CREATE TYPE reservation_state AS ENUM ('DRAFT', 'CONFIRMED', 'CANCELLED', 'EXPIRED');

CREATE TABLE users (
    id    bigserial PRIMARY KEY,
    email text NOT NULL UNIQUE
);

CREATE TABLE movies (
    id               bigserial PRIMARY KEY,
    title            text NOT NULL,
    duration_minutes int  NOT NULL CHECK (duration_minutes > 0)
);

CREATE TABLE halls (
    id   bigserial PRIMARY KEY,
    name text NOT NULL UNIQUE
);

-- The reserved resource.
CREATE TABLE seats (
    id          bigserial PRIMARY KEY,
    hall_id     bigint NOT NULL REFERENCES halls (id),
    row_label   text   NOT NULL,
    seat_number int    NOT NULL,
    UNIQUE (hall_id, row_label, seat_number)
);

CREATE TABLE screenings (
    id         bigserial   PRIMARY KEY,
    movie_id   bigint      NOT NULL REFERENCES movies (id),
    hall_id    bigint      NOT NULL REFERENCES halls (id),
    starts_at  timestamptz NOT NULL,
    ends_at    timestamptz NOT NULL,
    CHECK (ends_at > starts_at)
);

CREATE TABLE reservations (
    id           bigserial         PRIMARY KEY,
    user_id      bigint            NOT NULL REFERENCES users (id),
    screening_id bigint            NOT NULL REFERENCES screenings (id),
    state        reservation_state NOT NULL DEFAULT 'DRAFT',
    hold_until   timestamptz       NOT NULL,
    created_at   timestamptz       NOT NULL DEFAULT now(),
    confirmed_at timestamptz,
    cancelled_at timestamptz
);

-- One row per seat held by a reservation. A reservation may hold several seats.
--
-- screening_id and state are denormalised copies of the parent reservation's columns.
-- They exist for exactly one reason: PostgreSQL cannot build an index across two
-- tables, and we want the no-double-booking invariant to live in the database rather
-- than in application code. The cost is that both columns must be written whenever the
-- parent reservation changes state. ADR-005 records that trade.
CREATE TABLE reservation_seats (
    reservation_id bigint            NOT NULL REFERENCES reservations (id) ON DELETE CASCADE,
    seat_id        bigint            NOT NULL REFERENCES seats (id),
    screening_id   bigint            NOT NULL REFERENCES screenings (id),
    state          reservation_state NOT NULL,
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
