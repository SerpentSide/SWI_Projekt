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
| [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md) | Architecture sketch, ADR-001 … ADR-008, C03 Part A (AS-IS trace of Confirm Reservation) and the C05 Detailed Design of the Reservation lifecycle |
| [docs/evidence-and-evolution.md](docs/evidence-and-evolution.md) | C01 engineering spike, C02 evidence (specification → running app), C03 architecture evidence, why there is no C04 artifact, C05 DD verification evidence, the v0.3 change after C05 |
| [docs/operations-specification.md](docs/operations-specification.md) | C02: full spec of the four core operations (Create, Check Availability, Confirm, Cancel), the v0.2 Approve operation, and the shared business rules/invariants (BR-01 .. BR-04); baseline v0.3 |
| [docs/c02-review.md](docs/c02-review.md) | C02: the acceptance-gate check for each operation, extracted from the spec so it can be reviewed on its own |
| [docs/diagrams.md](docs/diagrams.md) | C02: use-case, lifecycle (state) and per-operation activity diagrams, with a table tracing each transition to spec text and a test |
| [docs/c02-change-impact.md](docs/c02-change-impact.md) | C02: impact analysis of the approval change (v0.1 → v0.2), done before the specification was edited |
| [docs/c03-architecture.md](docs/c03-architecture.md) | C03: drivers, responsibilities, decision question, alternatives, ADR-008 decision, context / static / runtime / sequence / class views, cross-view check, AS-IS → TO-BE delta |
| [docs/where-to-find-what.md](docs/where-to-find-what.md) | Navigation map: ADRs, drivers, operations, rules -> where they live in docs, code and tests |
| [docs/review.html](docs/review.html) | Team review page — every C01 decision with what it costs. Open it in a browser (double-click, no server needed). Written in Czech for the team. |

## The domain in one screen

**Resource:** `Seat` (hall, row, number) — occupied for the duration of the screening.
**Reservation:** one viewer's claim on 1..N seats of one screening.
**States:** `DRAFT` → `CONFIRMED` → `CANCELLED`, plus `EXPIRED` when a hold runs out. Seats that
need approval (v0.2) go `DRAFT` → `PENDING_APPROVAL` → `CONFIRMED` or `REJECTED`; the full
lifecycle is in [docs/diagrams.md](docs/diagrams.md).

```
create   : [start] -> DRAFT
confirm  : DRAFT -> CONFIRMED                       (no seat needs approval; until the screening starts, v0.3)
           DRAFT -> PENDING_APPROVAL                (a seat needs approval; until the screening starts, v0.3)
approve  : PENDING_APPROVAL -> CONFIRMED
reject   : PENDING_APPROVAL -> REJECTED
cancel   : DRAFT | PENDING_APPROVAL | CONFIRMED -> CANCELLED   (until the screening starts)
derived  : DRAFT (hold passed) -> EXPIRED;  PENDING_APPROVAL (screening started) -> EXPIRED
```

**Occupied** = a seat with a `CONFIRMED` reservation, a `DRAFT` one whose 15-minute hold is
still running, *or* a `PENDING_APPROVAL` one while the screening has not started. Both business
rules stand on this one definition.

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

**Boundary.** `NotificationService` (`src/cinema/notifications.py`) — an interface the
operations call after their transaction commits. Today's implementation delivers **browser
notifications**: messages go to a `notifications` table, and the frontend polls for them and
shows them with the browser's Notification API (bell icon in the header turns them on). A
third-party e-mail/SMS provider would be another implementation of the same interface.
A failed notification never fails a confirmed reservation.

| Event | Who is notified |
|---|---|
| confirm ends `CONFIRMED` | the viewer — "Rezervace potvrzena" |
| confirm ends `PENDING_APPROVAL` (VIP seat) | the viewer — "Rezervace uložena - čeká na schválení" |
| box office approves / rejects | the viewer — "Rezervace schválena" / "Rezervace zamítnuta" |
| a movie is added (`POST /movies`) | every user — "Nový film v programu" |

## CP1 walking skeleton

One end-to-end path, defined for CP1. **It runs today** — `tests/test_api.py::test_walking_skeleton`.

```
POST /reservations
  → validate          screening exists; seats belong to that screening's hall;
                      seats are not occupied; no-orphan rule holds
  → persist           INSERT reservation (state = DRAFT, hold_until = now + 15 min)
                      + one reservation_seats row per seat, in one BEGIN IMMEDIATE
                      transaction (concurrent creates are serialised by SQLite's write lock)
  → return            201 Created  { "reservation_id": 42, "state": "DRAFT", "hold_until": "..." }
  → automated check   pytest: POST returns 201 with an id
                             → GET /reservations/42 reports DRAFT
                             → GET /screenings/1/availability shows those seats occupied
```

Error responses on this path: `404` unknown screening or seat, `409` a seat is already
taken (also the loser of two simultaneous creates), `422` the no-orphan rule rejects
the selection. The `uq_confirmed_seat_per_screening` index (ADR-004) guards the later step,
confirm, where the `UniqueViolation` race lives.

## Running what exists today

The repository contains the schema, a simple FastAPI backend (`src/cinema/main.py`), a
React frontend (`src/frontend/`) and the C01 engineering spike.

On Windows, one command does everything (venv, dependencies, backend and frontend); Ctrl+C stops both:

```powershell
.\start.ps1
```

Or step by step:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -v          # API tests + the spike, SQLite files in tmp_path
./.venv/bin/uvicorn cinema.main:app --app-dir src --reload   # API on http://localhost:8000/docs
npm --prefix src/frontend ci && npm --prefix src/frontend run dev   # UI on http://localhost:5173
```

Expected result: **86 passed** — `test_c02_operations.py` 54, `test_api.py` 7, `test_lifecycle.py` 8,
`test_notifications.py` 6, `test_c05_transition.py` 5, `test_architecture.py` 4, `test_double_booking_spike.py` 2. Among them:

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
data: `seed.sql` plus random screenings and already-reserved seats from `seed.py`
(from today to the end of next month, so the window moves with the date of seeding). To wipe
it and reseed: `PYTHONPATH=src ./.venv/bin/python -m cinema.main` (needed after a schema
change, e.g. the `notifications` table — the API only creates the schema in an empty file).

The frontend calls the API through Vite's dev proxy (`/api` → `localhost:8000`), so start
the backend first.

| Endpoint | |
|---|---|
| `POST /auth/login` `{email}` | returns `user_id` (user created on first login, no password yet) |
| `GET /movies` | all movies |
| `GET /screenings?movie_id=&date=` | screenings, optionally filtered |
| `GET /screenings/{id}` | one screening |
| `GET /screenings/{id}/availability` | every seat of the hall with `occupied` |
| `POST /reservations` `{user_id, screening_id, seat_ids}` | 201 DRAFT hold · 404 · 409 taken · 422 orphan seat |
| `GET /reservations/{id}` | reservation with its seats |
| `GET /users/{id}/reservations` | a user's reservations |
| `POST /reservations/{id}/confirm` | DRAFT → CONFIRMED, or DRAFT → PENDING_APPROVAL if a seat needs approval (409 if expired, seat taken or the screening has started) |
| `POST /reservations/{id}/cancel` | → CANCELLED, returns `cancelled_at` (until the screening starts) |
| `POST /reservations/{id}/decision` `{decision: approve\|reject}` | box office: PENDING_APPROVAL → CONFIRMED / REJECTED |
| `POST /movies` `{title, duration_minutes, …}` | add a movie; notifies every user |
| `POST /users/{id}/notifications/deliver` | the browser's poll: undelivered notifications, marked delivered |
| `GET /health` | liveness check |

## Repository layout

```
README.md                            this file -- domain summary + walking skeleton
requirements.txt                     runtime deps (FastAPI)
requirements-dev.txt                 test deps
pytest.ini                           pytest settings (src on the path)
start.ps1                            Windows: venv, dependencies, backend and frontend in one command
docs/
  intent-and-change.md               Project Frame, future pressure, change + review loop
  operations-specification.md        C02: specification of OP-01 .. OP-05, rules BR-01 .. BR-04
  c02-review.md                      C02: acceptance-gate check of each operation
  diagrams.md                        C02: use-case, state and activity diagrams
  c02-change-impact.md               C02: impact analysis of the approval change
  architecture-and-decisions.md      architecture sketch, ADR-001 .. ADR-008, C03 Part A
  c03-architecture.md                C03: drivers, alternatives, views, AS-IS -> TO-BE delta
  evidence-and-evolution.md          C01 spike, C02 evidence, C03 evidence, C04 note, C05 evidence, v0.3 change
  where-to-find-what.md              navigation map (Czech): decisions, drivers, operations -> docs, code, tests
  review.html                        C01 team review page (Czech, open in a browser)
  trace_confirm.py                   C03 Part A runtime trace of the Confirm conflict (throw-away database)
src/
  cinema/
    schema.sql                       tables + the partial unique index that ADR-004 rests on
    seed.sql                         demo users, movies, halls, seats
    seed.py                          random screenings + already-reserved seats
    main.py                          the FastAPI app: routes, validation, error -> status code (HTTP layer)
    reservations.py                  Reservation Management: the only code that changes Reservation state (ADR-008)
    lifecycle.py                     C05: pure lifecycle policy -- allowed transitions, derived EXPIRED
    availability.py                  definition of "occupied", no-orphan rule
    db.py                            connection, write transaction, schema + seed loading
    notifications.py                 NotificationService interface + browser implementation, notify()
  frontend/                          React + Vite UI (see its own README)
tests/
  test_double_booking_spike.py       the executed C01 spike
  test_c02_operations.py             C02 acceptance tests: every operation, v0.2 approval, concurrency
  test_api.py                        walking skeleton + error paths
  test_notifications.py              who gets notified, delivery, failure isolation
  test_architecture.py               C03: only reservations.py writes Reservation state
  test_lifecycle.py                  C05: unit tests of the lifecycle policy
  test_c05_transition.py             C05: transition path, atomicity and BR-02 conflict
```
