# Cinema Seat Reservation

A reservation system for cinema seats, built for SWI. The reserved resource is an
**individual seat** for a given screening; a reservation may hold several seats at once.

| | |
|---|---|
| **Team** | *(fill in: team name)* |
| **Members** | *(fill in: 3–4 members)* |
| **Repository** | https://github.com/SerpentSide/SWI_Projekt |
| **Stack** | Python 3.11 · FastAPI · PostgreSQL 16 · pytest ([why](docs/architecture-and-decisions.md#adr-002--python-311--fastapi--postgresql)) |

## Documentation

| Document | Contains |
|---|---|
| [docs/intent-and-change.md](docs/intent-and-change.md) | Project Frame, selected future pressure, the C01 change + review loop |
| [docs/architecture-and-decisions.md](docs/architecture-and-decisions.md) | Architecture sketch and ADR-001 … ADR-007 |
| [docs/evidence-and-evolution.md](docs/evidence-and-evolution.md) | C01 engineering spike: question, method, measured result, decision |
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

Today the repository contains the schema and the C01 engineering spike. The API itself is
not implemented yet — that is CP1 work.

```bash
docker compose up -d                            # PostgreSQL 16 on localhost:55432
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -v          # runs the spike
```

Expected output:

```
tests/test_double_booking_spike.py::test_without_db_constraint_the_seat_is_sold_twice PASSED
tests/test_double_booking_spike.py::test_with_db_constraint_the_seat_is_sold_once     PASSED
```

The first test is meant to pass by *reproducing the double-booking bug* with the safety
index dropped; the second shows the index preventing it. Shut down with
`docker compose down -v`.

## Repository layout

```
README.md                            this file -- domain summary + walking skeleton
docker-compose.yml                   PostgreSQL 16 for local development and tests
requirements-dev.txt
docs/
  intent-and-change.md               Project Frame, future pressure, change + review loop
  architecture-and-decisions.md      architecture sketch, ADR-001 .. ADR-007
  evidence-and-evolution.md          C01 spike: question, method, result, decision
src/
  cinema/
    schema.sql                       tables + the partial unique index that ADR-004 rests on
tests/
  test_double_booking_spike.py       the executed C01 spike
```
