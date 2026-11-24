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
| **Box office operator** | Looks up a reservation, sees real occupancy of a screening, cancels on request. **Approves or rejects reservations for seats that need approval (v0.2).** |
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
| **Create reservation** | Validates the request, holds the chosen seats, returns a reservation in state `DRAFT` with a `hold_until` deadline. Only accepted while the screening has not started. |
| **Confirm reservation** | Only while the hold is still alive and (v0.3) the screening has not started. `DRAFT` -> `CONFIRMED` — the operation that makes the seat definitively sold — **unless a seat in the reservation requires approval; then `DRAFT` -> `PENDING_APPROVAL` and the seats stay held until a decision (v0.2).** |
| **Approve reservation** *(v0.2)* | A box office operator decides a `PENDING_APPROVAL` reservation: approve -> `CONFIRMED`, reject -> `REJECTED`. Only until the screening starts **and (v0.4, CR-C08) within 30 minutes of the confirm that made it pending**. |
| **Cancel reservation** | `DRAFT`, `PENDING_APPROVAL` or `CONFIRMED` -> `CANCELLED`, allowed until the screening starts. Frees the seats. |
| **Check availability** | Returns the seat map of a screening, each seat marked free or occupied. |

## Persistent state

**Reservation** — identity (`id`), owning `user_id`, target `screening_id`, the set of
reserved `seat_id`s, `state`, `hold_until`, `created_at`, `confirmed_at`,
`cancelled_at`, and (v0.4) `pending_until` — the approval deadline written when it enters
`PENDING_APPROVAL`.

**Seat (resource)** — identity (`id`), owning `hall_id`, `row_label`, `seat_number`, and (v0.2)
`requires_approval` — a fixed property of the seat, e.g. a VIP row. A reservation needs approval
if at least one of its seats does. Seats are static inventory; they are not modified by reservations. Occupancy is *derived*
from reservations, never stored on the seat.

Supporting state: **Screening** (`id`, `movie_id`, `hall_id`, `starts_at`, `ends_at`),
**Hall** (`id`, `name`), **Movie** (`id`, `title`, `duration_minutes`), **User** (`id`, `email`).

### Definition of "occupied"

This single definition is what both business rules stand on:

> A seat is **occupied** for a screening if there exists a reservation for that seat and
> screening which is either in state `CONFIRMED`, or in state `DRAFT` whose
> `hold_until` has not yet passed, or (v0.2) in state `PENDING_APPROVAL` while the screening
> has not started and (v0.4) its `pending_until` has not passed.

Without the second half, a hold would block nothing and would be decoration. A pending
approval blocks its seats for the same reason: otherwise someone else could sell them while
the approver decides, and there would be nothing left to approve.

**This is an exclusivity guarantee, not just a display rule.** At most one reservation may
hold a seat as a live `DRAFT` or as `CONFIRMED` at any moment — two different viewers must
never simultaneously hold the same seat, even during the 15-minute window. A viewer who
requests an already-occupied seat is rejected outright, and the seat does not become
available to a second viewer until the first viewer's hold expires or is cancelled.
`create` therefore needs the same kind of race-safety `confirm` already has (ADR-004): a
`SELECT`-then-insert check alone is not enough here for the same reason it was not enough
for confirm.

## State-changing operation

```
[start] --create-------------------------------------> DRAFT
DRAFT   --confirm  [no seat needs approval]-----------> CONFIRMED  (until the screening starts, v0.3)
DRAFT   --confirm  [a seat needs approval]------------> PENDING_APPROVAL      (v0.2; until the screening starts, v0.3)
DRAFT   --hold_until passes---------------------------> EXPIRED    (derived)
PENDING_APPROVAL --approve----------------------------> CONFIRMED             (v0.2)
PENDING_APPROVAL --reject-----------------------------> REJECTED              (v0.2)
PENDING_APPROVAL --screening starts undecided---------> EXPIRED    (derived)  (v0.2)
PENDING_APPROVAL --30 min after confirm undecided-----> EXPIRED    (derived)  (v0.4, CR-C08)
DRAFT | PENDING_APPROVAL | CONFIRMED --cancel---------> CANCELLED  (until the screening starts)
```

The drawn version (Mermaid) is in `docs/diagrams.md`.

The state-changing operation we care about is **becoming `CONFIRMED`** (`DRAFT` -> `CONFIRMED`,
or `PENDING_APPROVAL` -> `CONFIRMED` for seats that need approval): it is the moment a held
seat becomes a sold seat, and therefore the moment both business rules must hold.

`CANCELLED`, `EXPIRED` and `REJECTED` are terminal. `PENDING_APPROVAL` that is still undecided
when the screening starts, or (v0.4) 30 minutes after it started waiting, reads as `EXPIRED` (derived,
exactly like an expired hold).

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

> **Original C01 baseline.** The table and the e-mail/SMS provider below are the boundary as
> framed at C01. The system has since moved on — see *Current implementation* after the table.

| Call | When |
|---|---|
| `reservation_confirmed(reservation)` | After `DRAFT` -> `CONFIRMED` succeeds. |
| `hold_expiring_soon(reservation)` | Shortly before `hold_until` passes. |

The cinema does not own it; it is a third-party e-mail/SMS provider reached over HTTP.
It is therefore allowed to be slow, to fail, and to succeed twice. For CP1 it exists as a
stub behind an interface, so that the boundary is real in the code even though the provider
is not.

*Current implementation (C03 onwards).* `NotificationService` (`src/cinema/notifications.py`)
has one implementation, `BrowserNotifications`: messages go to a `notifications` table and the
frontend polls for them and shows them as browser notifications. Its calls are
`reservation_confirmed`, `reservation_pending_approval`, `reservation_approved`,
`reservation_rejected` and `new_movie` (see the README's notification table and OP-03/OP-05).
`hold_expiring_soon` is **not** implemented — with lazy expiry (ADR-003) there is no scheduler
to fire it. An e-mail/SMS provider would be another implementation of the same interface.

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

---

## C08 / CR-C08 Pending approval limit — Controlled Change Card

> **Status of this card.** Written down on 2026-11-24 together with its implementation (branch `c89`,
> C09), from the team's C08 analysis. The analysis was done on the C08 reporting baseline `afd8a17`; the
> code was unchanged in the lifecycle until `3b8f4bf` (PR #15 only added roles and the demo top-up), so
> `3b8f4bf` is the before baseline used below. **The peer review line is a placeholder until a second team
> member fills it in.** Test records are in [`evidence-and-evolution.md`](evidence-and-evolution.md),
> "C08 - Before baseline and TC-C08-01/02" and "C09 - Integration, CI and Release Record".

**Request source and concrete project need.** Box office (stakeholder: cinema operator). A VIP-row (`J`)
request that nobody decides keeps its seats blocked until the screening starts — possibly for days — and can
then still be approved long after the viewer has given up. Need: *a pending request that nobody decided
within 30 minutes must not later become a valid booking, and its seats must go back on sale.*

**Before commit/tag + spec/BR/ADR + schema/config.** `main` @ `3b8f4bf` (PR #15); spec v0.3 (OP-03, OP-05,
Definition of Occupied, BR-01..BR-04); ADR-003 (lazy expiry), ADR-004 (unique index), ADR-008 (one owner of
state); schema = `src/cinema/schema.sql` @ `3b8f4bf` (no `pending_until`); config: `CINEMA_DB` only; runtime
Python 3.11+ (verified on 3.13.18), SQLite 3.53.1; `python -m pytest -q` → 97 passed.

**Old accepted behaviour / observable limitation.** `PENDING_APPROVAL` reads as `EXPIRED` only when the
screening starts (OP-05, v0.2: "Approval is delayed: a reservation stays PENDING_APPROVAL for hours").
There is no other time limit.

**New intended behaviour, scope, out of scope.** A `PENDING_APPROVAL` request expires 30 minutes after it
entered `PENDING_APPROVAL` (the confirm instant), measured by the server clock in UTC. Expired requests can
be neither approved, rejected nor cancelled, read as `EXPIRED`, and no longer occupy their seats.
*Out of scope:* a background worker, notifying anybody at expiry, a configurable or per-hall limit, an
owner check, UI redesign.

**Acceptance conditions** (expected results fixed before the implementation; `T` = confirm instant):

| AC | Kind | Condition |
|---|---|---|
| AC-01 | positive | Approve at age 29:59 → `200 CONFIRMED`; committed state `CONFIRMED` on both tables, `confirmed_at` = approve instant |
| AC-02 | negative | Approve at age 30:01 → `409`; the reservation is never `CONFIRMED`; it reads `EXPIRED`; its seats read free |
| AC-03 | boundary | At exactly `T + 30:00` the request is **already expired** (half-open, age ≥ 30:00 is late — the same convention as `hold_until`) → approve `409` |
| AC-04 | regression | BR-02: no seat ever has two `CONFIRMED` reservations; after an expiry another viewer can take and confirm the seat once |
| AC-05 | data | Entering `PENDING_APPROVAL` records the deadline (`pending_until = T + 30 min`), readable via `GET /reservations/{id}` |

Unchanged (regression basis): Create, Check Availability, Confirm of ordinary seats, Cancel, approve/reject
*within* the limit, roles (`403` before `409`), a pending request outlives the 15-minute `DRAFT` hold,
notifications. C08 test cases TC-C08-01 (approve next to a confirmed seat) and TC-C08-02 (approve onto a
confirmed seat → `409`, nothing written).

**Impact analysis method / evidence reviewed.** Started from AC-02, found the owner of the decision
(`lifecycle.plan` / `effective_state`, called only from `reservations.transition`, ADR-008), followed the data
to `reservations`/`reservation_seats`, to every reader of "occupied" (`availability.OCCUPIED_SEATS_SQL`, used by
create and the seat map), to the API, frontend, notifications, schema/startup and tests. Writer paths checked
with `tests/test_architecture.py`: only `reservations.py` (and the demo seeder, which writes only `CONFIRMED`).

| Dimension / exact artifact | CHANGE/CHECK/NO IMPACT + why | Needed delta/verification | Owner/evidence |
|---|---|---|---|
| Spec/BR/state: OP-03, OP-05, Definition of Occupied, state diagram | CHANGE — a new edge `PENDING_APPROVAL → EXPIRED` at the deadline | Spec v0.4, `diagrams.md` v0.4 | TC-C09-02..04, U8 |
| Architecture/ownership/interfaces: ADR-003, ADR-008 | CHECK — still one writer (Reservation Management), expiry still derived | ADR-009 records the choice; conformance test unchanged | `test_architecture.py` |
| Detailed design/code: `lifecycle.effective_state`, `ReservationFacts`, `reservations._change_state`, `availability.OCCUPIED_SEATS_SQL` | CHANGE — new fact `pending_until`, written by confirm, compared in two places | Same half-open predicate in both places | U1 (1280 combos), U8, TC-C09-03 (seat map) |
| Data/schema/migration: `reservations` | CHANGE — nullable column `pending_until`; existing DBs need it | `db.add_pending_until()` at startup, idempotent; legacy pending rows (NULL) read `EXPIRED` (fail-closed) | TC-C09-07, smoke on a before-DB |
| API: `/confirm`, `/decision`, `/cancel`, `GET /reservations/{id}` | CHECK — status codes unchanged (`409` as for any expired state); GET now also returns `pending_until` | None beyond the spec text | TC-C09-01, -03, -04 |
| Authorisation/roles | NO IMPACT — role check precedes state check, unchanged | Regression only | `test_c02_operations.py::TestApprove` |
| Frontend | CHECK — no state shown except the "waiting for approval" message | Message states the 30-minute limit | `npm run build` |
| Notifications | NO IMPACT — nothing is sent at expiry (no worker); pending notification unchanged | Known limitation | `test_notifications.py` |
| Configuration/CI/release | CHECK — limit is a code constant (`reservations.APPROVAL_LIMIT`), no new env var; no CI existed | Add `scripts/check.sh` + GitHub Actions workflow | C09 record |
| Worker / import / admin writer | NO IMPACT — no such write path exists in this system | — | `test_architecture.py` |
| Operation/recovery | CHECK — pending requests silently drop out of "occupied" after 30 min; rollback of code keeps the column | Document rollback limits | C09 record |

**Alternatives and comparable criteria.**

| Option | Meets AC? | Benefit | Cost/failure/rollback | Verification |
|---|---|---|---|---|
| A: lazy expiry at a stored deadline (`pending_until`), checked by the existing owner | Yes (AC-01..05) | Small change, no worker, same pattern as the `DRAFT` hold (ADR-003) | One column + startup upgrade; expired request only "disappears" when read; nobody is notified | 29:59 / 30:00 / 30:01 integration tests, BR-02 regression, migration test |
| B: scheduler + guard | Yes | Proactive status change; could notify | Second writer (reopens ADR-008), scheduler failure/retry/race, ops weight | Worker + restart + race tests |
| C: no code change, the box office checks the queue manually | No (AC-02 not guaranteed) | Zero code | Relies on discipline; late approvals still possible | Process check only |

**Inaction consequences / feasible compensation.** VIP seats stay blocked for days by abandoned requests
and can be approved after the viewer has left; compensation would be manual rejects by the box office (C).

**Peer reviewer and challenged assumption.** _PLACEHOLDER — to be filled in by a second team member
(name, date, the assumption they challenged, e.g. "is a 30:00 approve late?" or "fail-closed for legacy
rows")._

**Decision.** **ACCEPT option A** — proposed by Š. Adámek (owner), 2026-11-24. Rationale: meets every AC with
the smallest delta and follows the existing lazy-expiry design; B's proactive behaviour was not requested.
_Team confirmation pending (see peer reviewer line and the v0.4 sign-off in `diagrams.md`)._

**If ACCEPT: smallest change and acceptance tests to perform in C09.** Delta: `pending_until` column +
upgrade, confirm writes it, `effective_state` and `OCCUPIED_SEATS_SQL` compare it, spec/diagrams v0.4.
Tests: TC-C09-01..07 (new), TC-C08-01/02 and the full existing suite (regression).

**Open uncertainty and next trigger.** No notification at expiry; the 30-minute value is not configurable;
clock skew between server instances is N/A (one SQLite server). Reopen when the cinema asks to be told
about expiries or wants a different limit per hall.
