# Architecture and Decisions

Short architecture notes plus the decision records for everything that was a real choice.
Each ADR says what we picked, what it costs, and what would make us change our mind.

## Architecture at a glance

```
          HTTP
   Viewer ────► API layer (FastAPI)
                   │   request/response models, HTTP status mapping
                   ▼
                Domain layer
                   │   Reservation lifecycle, overlap rule, no-orphan rule
                   ▼
                Persistence layer        ──────►  SQLite
                   │   repositories, transactions      ▲
                   │                                   │
                   │                          uq_confirmed_seat_per_screening
                   │                          = the no-double-booking invariant
                   ▼
              NotificationService (boundary, stubbed)
```

Three layers, one outbound boundary. The rule that must never break is enforced at the
bottom, in the database, for the reason recorded in ADR-004.

---

## ADR-001 — The reserved resource is a Seat

**Status:** accepted

**Context.** "Cinema reservation" can be modelled with at least three different resources,
and the choice decides whether the assignment's mandatory rule ("two confirmed reservations
for the same resource must not overlap in time") even applies.

**Decision.** `Resource = Seat` — a physical seat in a hall, occupied for the duration of
the screening it was booked for.

**Alternatives rejected.**

| Alternative | Why not |
|---|---|
| `Resource = Hall`, reservation = a scheduled screening | Makes the user a cinema programmer, not a moviegoer. The interesting domain (seat choice) disappears. |
| `Resource = Screening with capacity`, reservation = N seats | The mandatory rule stops being about time overlap and becomes a capacity rule. Different rule than the one we were asked to model. |

**Consequences.** The overlap rule applies literally and needs no reinterpretation. The
cost is that occupancy must always be *derived* from reservations rather than stored on
the seat, otherwise two sources of truth would drift.

---

## ADR-002 — Python 3.11 + FastAPI + SQLite

**Status:** accepted

**Context.** The brief names Java 21 + Spring Boot + Maven + PostgreSQL as the supported
stack and allows any other stack provided the team supports it themselves and justifies
the choice. This ADR is that justification.

**Decision.** Python + FastAPI + SQLite (via the standard-library `sqlite3` module),
tested with pytest.

**Why.** The team is faster in Python than in Java, and in a course where the deliverable
is a *design* under a *time budget*, framework familiarity converts directly into domain
work. FastAPI gives request validation and an OpenAPI description without extra
machinery. We deviate from the recommended PostgreSQL as well: SQLite is a single file
with no server to install, configure, or containerise, which matters for a team of
students who need to be productive from commit one. It still gives us the one guarantee
ADR-004 depends on — a partial unique index — so nothing about the invariant we care about
is lost by leaving PostgreSQL behind.

**Costs we accept.**

- No compile-time type checking. Mitigation: type hints plus tests around the domain rules.
- We support the stack ourselves; nothing about it is pre-blessed by the course.
- SQLite allows only one writer at a time for the whole database file. Concurrent readers
  are fine, but concurrent confirms serialise on a lock instead of interleaving the way
  they would under PostgreSQL's MVCC. ADR-004's spike measures what this means in practice
  for the no-double-booking invariant.
- SQLite has no native `ENUM` or timezone-aware timestamp type; `schema.sql` uses `TEXT`
  with `CHECK` constraints and ISO-8601 strings instead.

**What would change our mind.** If the selected future pressure (a premiere on-sale burst,
several hundred concurrent confirms) turns out to need real concurrent writers rather than
serialised ones — something the spike in ADR-004 does not test at that scale — moving to
PostgreSQL is still cheap this early: the domain model and all three documents are
stack-independent.

---

## ADR-003 — Hold expiry is evaluated lazily, with no background job

**Status:** accepted

**Context.** A `DRAFT` reservation holds its seats for 15 minutes. Something has to make
the hold stop mattering once it passes.

**Decision.** No scheduler. `hold_until` is compared to the current time whenever a
reservation is read or confirmed. A `DRAFT` row whose hold has passed is treated as
`EXPIRED` and stops occupying its seats.

**Alternative rejected.** A periodic job flipping stale `DRAFT` rows to `EXPIRED`. It buys
no behavioural difference — a hold that nobody observes has no effect either way — and it
adds a scheduler, a failure mode, and an operational thing to monitor.

**Consequences.** The `EXPIRED` state may be *implicit* in the table for a while: a row can
read `DRAFT` while behaving as expired. Every availability query must therefore include the
`hold_until > now()` predicate, and forgetting it in one query is a plausible bug. We accept
that and keep the predicate in a single shared query helper rather than repeating it.

**What would change our mind.** A reporting or analytics need for accurate `EXPIRED`
timestamps, which lazy evaluation cannot provide.

---

## ADR-004 — Double-booking is prevented by a database constraint, not application code

**Status:** accepted — **backed by the C01 engineering spike**

**Context.** Two viewers may confirm the same seat at the same instant. We did not know
whether a `SELECT`-then-write availability check in the application survives that.

**Decision.** The invariant lives in SQLite:

```sql
CREATE UNIQUE INDEX uq_confirmed_seat_per_screening
    ON reservation_seats (screening_id, seat_id)
    WHERE state = 'CONFIRMED';
```

The application's availability `SELECT` is kept for user experience only and is explicitly
**not** a correctness guarantee. `IntegrityError` (`UNIQUE constraint failed`) on confirm
is mapped to **HTTP 409**.

**Evidence.** Measured, not assumed. With the index dropped, two concurrent confirms both
succeeded and the seat was sold twice; with it in place, exactly one succeeded. The
application-level check returned its "seat taken" outcome zero times in either run. Full
transcript and method in [`evidence-and-evolution.md`](evidence-and-evolution.md).

**A wrinkle specific to SQLite.** Unlike PostgreSQL, SQLite only ever lets one connection
hold the write lock, so the two confirms cannot truly interleave — the second writer blocks
until the first commits. The spike shows that this does **not** make the application-level
`SELECT` safe: both threads still read "free" before either one writes, so the outcome the
invariant has to prevent is the same, just reached by a different path (a blocked writer
that then fails the uniqueness check, instead of two writers whose commits race). See
`evidence-and-evolution.md` for what this means for ADR-002's stated cost.

**Alternative rejected.** Pessimistic locking with `SELECT ... FOR UPDATE`-style locking
reads. The spike shows the index alone is sufficient, and SQLite's single-writer model
already serialises writes for us, so explicit locking would only add complexity for no
extra correctness during precisely the premiere burst of our selected future pressure.

**The assumption this rests on.** The index is keyed on `screening_id`, not on a time range.
It is equivalent to the time-based rule in the Project Frame **only while two screenings in
one hall never overlap**. That assumption is recorded in
[`intent-and-change.md`](intent-and-change.md). If overlapping screenings ever become
possible, this index silently stops enforcing the stated rule. SQLite has no exclusion
constraint (PostgreSQL's `EXCLUDE USING gist` is not available here), so the fix would have
to be an application-level check over stored time ranges, done inside the same transaction
as the confirm and re-verified by re-reading under the write lock — a real loss of the
database-level guarantee this ADR currently relies on.

We did not build that now because the situation cannot currently arise, and because it
would reintroduce exactly the "does the check survive concurrency" question this spike was
run to answer.

---

## ADR-005 — `reservation_seats` denormalises `screening_id` and `state`

**Status:** accepted

**Context.** ADR-004 puts the invariant in a unique index. SQLite (like PostgreSQL) cannot
index across two tables, and both columns the index needs live on the parent `reservations`
row.

**Decision.** Copy `screening_id` and `state` onto each `reservation_seats` row.

**Consequences.** This is a genuine denormalisation and we are not pretending otherwise:
every state transition must now write *both* tables, and if one write is forgotten the index
protects the wrong thing. We accept it because the alternative is losing the database-level
guarantee that ADR-004 is built on, and a missed update is a far more visible bug than a
silent double-booking. The transition is confined to a single repository method so there is
exactly one place to get it right — and a database trigger keeping the two in sync is the
obvious hardening if that discipline ever slips.

---

## ADR-006 — The no-orphan rule is evaluated at create, and row ends count as occupied

**Status:** accepted, with a known open question

**Context.** "A reservation must not leave exactly one free seat isolated between occupied
seats in the same row" leaves two things undefined: *when* it is checked, and whether the
end of a row behaves like an occupied neighbour.

**Decision.**

1. Evaluated at **create**, against the Project Frame's definition of *occupied* — so live
   `DRAFT` holds count.
2. **The ends of a row count as occupied.** A single free seat next to the aisle is an
   orphan too.

**Why.** Checking at create tells the viewer the truth at the moment they choose, instead of
accepting their choice and rejecting it at payment time. Counting row ends matches the
commercial reason for the rule — a lone seat at the edge is just as unsellable as a lone
seat in the middle.

**Consequences.** Point 2 has a consequence worth stating plainly: **seat 2 of an
otherwise empty row can never be reserved**, because it strands seat 1. This is intended,
but it is the kind of thing that looks like a bug to someone who has not read this ADR.

Point 1 carries the risk recorded as the Project Frame's *Unknown*: a reservation can be
rejected because of a hold that expires seconds later. We have no data to choose better,
so we picked the option that never shows a viewer a seat map they cannot act on.

**What would change our mind.** Observed false rejections in real use, or a cinema operator
telling us aisle-adjacent singles do sell.

---

## ADR-007 — No frontend in CP1

**Status:** accepted

**Context.** A seat map is the obvious UI for this domain and is tempting to start early.

**Decision.** CP1 delivers the HTTP API and its automated check only. No frontend.

**Why.** The CP1 walking skeleton is defined as `POST /reservations → validate → persist →
return reservation ID → automated check`. A frontend adds a second stack and a second CI
path without advancing any part of that path. It is planned for after the skeleton actually
runs.

**Consequences.** Until then the seat map exists only as the `check availability` response,
and the domain rules are demonstrated by tests rather than by a picture.
