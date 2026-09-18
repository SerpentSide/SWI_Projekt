# Project Frame

## Reservation domain

**Cinema seat reservation.**

We reserve an **individual seat for a specific screening**. The reserved resource is
the physical `Seat` (hall, row, number), which is occupied for the duration of the
screening it was booked for. A single `Reservation` may hold several seats at once,
because people book for a group.

We deliberately did *not* model the resource as "the screening" or "the hall":

- `Resource = Hall` would make the users cinema programmers, not moviegoers.
- `Resource = Screening with capacity` would turn the mandatory overlap rule into a
  capacity rule, which is a different rule than the assignment asks for.
- `Resource = Seat` makes the mandatory overlap rule apply literally: seat `A5` must
  not have two confirmed reservations whose time windows overlap.

## Purpose

The system lets a moviegoer pick and hold specific seats for a screening online,
so that the seat they chose is guaranteed to be theirs when they arrive. It exists
to remove double-booking and queueing at the box office, and to give the cinema a
reliable picture of which seats are actually sold before a screening starts.

## Users / Stakeholders

| Role | What they do with the system |
|---|---|
| **Viewer** (moviegoer) | Browses seat availability, creates a reservation, confirms or cancels it. |
| **Box office operator** | Looks up a reservation, sees real occupancy of a screening, cancels on request. |
| **Cinema operator** (business stakeholder) | Cares that seats are not double-booked and that unsold single seats are minimised. |

Out of scope for now: the programmer/dramaturg who schedules screenings. We treat the
screening schedule as given input (see *Assumption*).

## Core concepts

| Concept | Meaning |
|---|---|
| `Seat` | **The reserved resource.** Belongs to one hall; identified by hall + row + number. |
| `Reservation` | One viewer's claim on 1..N seats of one screening. Has identity, time window, state. |
| `User` | The viewer who creates the reservation. |
| `Screening` | A showing of one movie in one hall, starting at a given time. Supplies the reservation's time window. |
| `Hall` | A room with a fixed seat layout. |
| `Movie` | Title and running time. Determines how long a screening occupies its hall. |

A reservation's time window is **not entered by the user** — it is derived from the
screening: `[screening.starts_at, screening.starts_at + movie.duration]`.

## Core operations

| Operation | Effect |
|---|---|
| **Create reservation** | Validates the request, holds the chosen seats, returns a reservation in state `DRAFT` with a `hold_until` deadline. |
| **Confirm reservation** | `DRAFT` -> `CONFIRMED`, only while the hold is still alive. This is the operation that makes the seat definitively sold. |
| **Cancel reservation** | `DRAFT` or `CONFIRMED` -> `CANCELLED`, allowed until the screening starts. Frees the seats. |
| **Check availability** | Returns the seat map of a screening, each seat marked free or occupied. |

## Persistent state

**Reservation** — identity (`id`), owning `user_id`, target `screening_id`, the set of
reserved `seat_id`s, `state`, `hold_until`, `created_at`, `confirmed_at`,
`cancelled_at`.

**Seat (resource)** — identity (`id`), owning `hall_id`, `row_label`, `seat_number`.
Seats are static inventory; they are not modified by reservations. Occupancy is *derived*
from reservations, never stored on the seat.

Supporting state: **Screening** (`id`, `movie_id`, `hall_id`, `starts_at`, `ends_at`),
**Hall** (`id`, `name`), **Movie** (`id`, `title`, `duration_minutes`), **User** (`id`, `email`).

### Definition of "occupied"

This single definition is what both business rules stand on:

> A seat is **occupied** for a screening if there exists a reservation for that seat and
> screening which is either in state `CONFIRMED`, or in state `DRAFT` whose
> `hold_until` has not yet passed.

Without the second half, a hold would block nothing and would be decoration.

## State-changing operation

```
          create()                        confirm()
   O ---------------> DRAFT ----------------------------> CONFIRMED
                        |   hold_until = now + 15 min           |
     hold expired       |                                       | cancel()
                        +-------------> EXPIRED                 | (until screening starts)
                        |                                       |
          cancel()      +-------------> CANCELLED <-------------+
```

The state-changing operation we care about is **`DRAFT` -> `CONFIRMED`**: it is the
moment a held seat becomes a sold seat, and therefore the moment both business rules
must hold.

`CANCELLED` and `EXPIRED` are terminal.

**Hold expiry is evaluated lazily.** There is no background job. `hold_until` is compared
against the current time when a reservation is read or confirmed. A `DRAFT` row whose hold
has passed is treated as `EXPIRED` and stops occupying its seats. We chose this because a
scheduler is real operational weight for no behavioural gain at this stage.

## Common business rule

> **Two confirmed reservations for the same seat must not overlap in time.**

Formally: for any seat `s`, there must be no two reservations `r1 != r2`, both in state
`CONFIRMED`, both containing `s`, whose time windows
`[starts_at, ends_at)` intersect.

## Domain-specific business rule

> **No orphan seat.** A reservation must not leave exactly one free seat isolated
> between occupied seats in the same row.

Formally: after applying the reservation, for every row it touches, there must be no
maximal run of free seats of length exactly 1. **The ends of a row count as occupied**
for this purpose, so a single free seat next to the aisle is also an orphan.

```
Row A, 8 seats.  [X] occupied  [ ] free  [R] being reserved

  [X][X][ ][ ][ ][ ][X][X]   starting point

  [X][X][R][R][ ][ ][X][X]   OK    - free run A5-A6 has length 2
  [X][X][R][ ][R][ ][X][X]   REJECT - A4 is an orphan
  [X][X][ ][R][R][R][X][X]   REJECT - A3 is an orphan
```

Why the cinema wants this: an isolated single seat is almost never sold, so every orphan
created is a seat of lost revenue for that screening.

**When it is evaluated:** at `create`, against the definition of *occupied* above — so
live `DRAFT` holds count as occupied. See *Unknown* for why this is not obviously right.

## External / system boundary

**`NotificationService`** — one outbound dependency.

| Call | When |
|---|---|
| `reservation_confirmed(reservation)` | After `DRAFT` -> `CONFIRMED` succeeds. |
| `hold_expiring_soon(reservation)` | Shortly before `hold_until` passes. |

The cinema does not own it; it is a third-party e-mail/SMS provider reached over HTTP.
It is therefore allowed to be slow, to fail, and to succeed twice. For CP1 it exists as a
stub behind an interface, so that the boundary is real in the code even though the provider
is not.

**A failed notification must never fail a confirmed reservation.** Selling the seat is the
business outcome; telling the viewer about it is a side effect.

## Assumption

**We assume that two screenings in the same hall never overlap in time**, because the
programming system that produces the schedule already guarantees it.

This assumption is load-bearing and we want it on the record: our enforcement of the
overlap rule is a uniqueness constraint per `(screening, seat)`, not a time-range
constraint. That is equivalent to the time-based rule *only while* this assumption holds.
If overlapping screenings ever became possible, our constraint would silently stop
enforcing the rule as stated. See `docs/architecture-and-decisions.md`, ADR-004.

## Unknown

**We do not know whether the no-orphan rule should be evaluated against held seats or
only against confirmed ones.**

Evaluating against live `DRAFT` holds (what we chose) gives the viewer a truthful seat map,
but it can reject a legitimate reservation because of a hold that expires thirty seconds
later. Evaluating only against `CONFIRMED` avoids that false rejection, but then two viewers
holding seats concurrently can jointly create an orphan that neither of them was shown.

We have no usage data to decide which failure is worse. This is a real product question,
not a technical one, and it needs a cinema operator to answer it.

---

## Selected future pressure

**Category: Q — Quality / Scale**

**Concrete pressure:**
When tickets for a major premiere go on sale, several hundred viewers try to reserve seats
for the *same screening* within a few seconds of each other, and a large share of them aim
at the same small set of "best" seats in the middle of the hall.

**Why it is relevant to our reservation system:**
Cinema traffic is not spread out — it is bursty and concentrated on very few screenings.
That burst attacks precisely the operation our correctness depends on, `DRAFT -> CONFIRMED`
for a specific seat. Under that pressure, an availability check that merely runs
`SELECT ... WHERE seat is free` before inserting is not enough: two requests can both read
"free" before either writes. The pressure therefore decides an architectural question —
whether protection against double-booking lives in the application or in the database.

That question is exactly what our C01 engineering spike goes and answers; see
`docs/evidence-and-evolution.md`.

We are **not** implementing this pressure in C01. It is recorded here to steer later work.

---

## C01 change and review loop

| | |
|---|---|
| **Issue / task** | `C01 engineering spike: does the database prevent double-booking of a seat?` |
| **Change** | Added the reservation schema, the partial unique index on `(screening_id, seat_id) WHERE state = 'CONFIRMED'`, and a concurrency test that drives two simultaneous transactions at the same seat. |
| **Author** | *Stanislav Urban* |
| **Reviewer** | *Šimon Adámek* |
| **Reviewed before integration** | Yes — opened as a pull request from branch `c01-project-frame-and-spike`; the reviewer read the diff and the spike output before the branch was merged. |
| **Integrated** | Merged into `main`. |

The review loop was deliberately attached to the spike rather than to a throwaway change,
so that the change a second person had to understand was the one that actually carries
risk.
