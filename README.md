# Cinema Seat Reservation

A reservation system for cinema seats, built for SWI. The reserved resource is an
**individual seat** for a given screening; a reservation may hold several seats at once.

| | |
|---|---|
| **Members** | *Jan Procházka (PRO0387), Jiří Ševeček (SEV0181), Šimon Adámek (ADA0306), Stanislav Urban (URB0283)* |
| **Repository** | https://github.com/SerpentSide/SWI_Projekt |
| **Stack** | Python 3.11 · FastAPI · SQLite · pytest ([why](docs/architecture-and-decisions.md#adr-002--python-311--fastapi--sqlite)) |

## Documentation

| Document | Contains |
|---|---|
| [docs/intent-and-change.md](docs/intent-and-change.md) | Project Frame, selected future pressure, the C01 change + review loop |
| [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md) | Architecture sketch and ADR-001 … ADR-007 |
| [docs/evidence-and-evolution.md](docs/evidence-and-evolution.md) | C01 engineering spike: question, method, measured result, decision |
| [docs/operations-specification.md](docs/operations-specification.md) | C02: full spec of the four core operations (Create, Check Availability, Confirm, Cancel) plus the shared business rules/invariants (BR-01 .. BR-04) |
| [docs/c02-review.md](docs/c02-review.md) | C02: the acceptance-gate check for each operation, extracted from the spec so it can be reviewed on its own |
| [docs/review.html](docs/review.html) | Team review page — every C01 decision with what it costs. Open it in a browser (double-click, no server needed). Written in Czech for the team. |

## The domain in one screen

**Resource:** `Seat` (hall, row, number) — occupied for the duration of the screening.
**Reservation:** one viewer's claim on 1..N seats of one screening.
**States:** `DRAFT` → `CONFIRMED` → `CANCELLED`, plus `EXPIRED` when a hold runs out.

```
          create()                        confirm()
   O ---------------> DRAFT ----------------------------> CONFIRMED
                        |   hold_until = now + 15 min           |
     hold expired       |                                       | cancel()
                        +-------------> EXPIRED                 | (until screening starts)
                        |                                       |
          cancel()      +-------------> CANCELLED <-------------+
```

**Occupied** = a seat with a `CONFIRMED` reservation, *or* a `DRAFT` one whose 15-minute
hold is still running. Both business rules stand on this one definition.

**Common rule.** Two confirmed reservations for the same seat must not overlap in time.
Enforced by the database — see [ADR-004](docs/architecture-and-decisions.md#adr-004--double-booking-is-prevented-by-a-database-constraint-not-application-code).

**Domain-specific rule — no orphan seat.** A reservation must not leave exactly one free
seat isolated between occupied seats in the same row. The ends of a row count as occupied.

```
Row A, 8 seats.  [X] occupied   [ ] free   [R] being reserved

  [X][X][ ][ ][ ][ ][X][X]   starting point
  [X][X][R][R][ ][ ][X][X]   OK
  [X][X][R][ ][R][ ][X][X]   REJECT -- A4 would be orphaned
  [X][X][ ][R][R][R][X][X]   REJECT -- A3 would be orphaned
```

**Boundary.** `NotificationService` — a third-party provider reached over HTTP, stubbed
behind an interface for CP1. A failed notification never fails a confirmed reservation.

## CP1 walking skeleton

One end-to-end path. **Defined now, runnable after C03 / before C04.**

```
POST /reservations
  → validate          screening exists; seats belong to that screening's hall;
                      seats are not occupied; no-orphan rule holds
  → persist           INSERT reservation (state = DRAFT, hold_until = now + 15 min)
                      + one reservation_seats row per seat, in one transaction,
                      guarded by uq_confirmed_seat_per_screening
  → return            201 Created  { "reservation_id": 42, "hold_until": "..." }
  → automated check   pytest: POST returns 201 with an id
                             → GET /reservations/42 reports DRAFT
                             → GET /screenings/1/availability shows those seats occupied
```

Error responses on this path: `404` unknown screening or seat, `409` a seat is already
taken (including the `UniqueViolation` race of ADR-004), `422` the no-orphan rule rejects
the selection.

## Running what exists today

The repository contains the schema, a simple FastAPI backend (`src/cinema/main.py`), a
React frontend (`src/frontend/`) and the C01 engineering spike.

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -v          # API tests + the spike, SQLite files in tmp_path
./.venv/bin/uvicorn cinema.main:app --app-dir src --reload   # API on http://localhost:8000/docs
npm --prefix src/frontend ci && npm --prefix src/frontend run dev   # UI on http://localhost:5173
```

Expected test output:

```
tests/test_api.py::test_walking_skeleton PASSED
tests/test_api.py::test_taken_seat_is_409 PASSED
tests/test_api.py::test_orphan_seat_is_422 PASSED
tests/test_api.py::test_unknown_screening_or_seat_is_404 PASSED
tests/test_api.py::test_seed_has_no_hall_overlaps_or_orphans PASSED
tests/test_api.py::test_double_confirm_is_409 PASSED
tests/test_double_booking_spike.py::test_without_db_constraint_the_seat_is_sold_twice PASSED
tests/test_double_booking_spike.py::test_with_db_constraint_the_seat_is_sold_once     PASSED
```

The first spike test is meant to pass by *reproducing the double-booking bug* with the
safety index dropped; the second shows the index preventing it. No external service to
start or tear down — SQLite is a file, created fresh per test by pytest's `tmp_path` fixture.

The API keeps its data in `cinema.sqlite3` in the repository root (override with the
`CINEMA_DB` environment variable). On first start it creates the schema and loads demo
data: `seed.sql` plus random screenings and already-reserved seats from `seed.py`. To wipe
it and reseed: `PYTHONPATH=src ./.venv/bin/python -m cinema.main`.

The frontend calls the API through Vite's dev proxy (`/api` → `localhost:8000`), so start
the backend first.

| Endpoint | |
|---|---|
| `POST /auth/login` `{email}` | returns `user_id` (user created on first login, no password yet) |
| `GET /movies` | all movies |
| `GET /screenings?movie_id=&date=` | screenings, optionally filtered |
| `GET /screenings/{id}/availability` | every seat of the hall with `occupied` |
| `POST /reservations` `{user_id, screening_id, seat_ids}` | 201 DRAFT hold · 404 · 409 taken · 422 orphan seat |
| `GET /reservations/{id}` | reservation with its seats |
| `GET /users/{id}/reservations` | a user's reservations |
| `POST /reservations/{id}/confirm` | DRAFT → CONFIRMED (409 if expired or seat taken) |
| `POST /reservations/{id}/cancel` | → CANCELLED (until the screening starts) |

## Repository layout

```
README.md                            this file -- domain summary + walking skeleton
requirements.txt                     runtime deps (FastAPI)
requirements-dev.txt                 test deps
docs/
  intent-and-change.md               Project Frame, future pressure, change + review loop
  architecture-and-decisions.md      architecture sketch, ADR-001 .. ADR-007
  evidence-and-evolution.md          C01 spike: question, method, result, decision
src/
  cinema/
    schema.sql                       tables + the partial unique index that ADR-004 rests on
    seed.sql                         demo users, movies, halls, seats
    seed.py                          random screenings + already-reserved seats
    main.py                          the FastAPI app
  frontend/                          React + Vite UI (see its own README)
tests/
  test_double_booking_spike.py       the executed C01 spike
  test_api.py                        walking skeleton + error paths
```
