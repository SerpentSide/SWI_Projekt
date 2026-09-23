# Operations Specification (C02)

This document is the complete minimum-behavior specification for the four core operations
named in the Project Frame ([`intent-and-change.md`](intent-and-change.md#core-operations)):
**Create Reservation**, **Check Availability**, **Confirm Reservation**, **Cancel
Reservation**. It is what a baseline application implements and what its acceptance tests
check. There is no fifth operation. In particular there is no `Approve`: confirming a
reservation is the only state-changing acceptance step in this baseline, and nothing here
models a human approval workflow — if one is ever needed it is a distinct, later change, not
a rename of Confirm.

Each slice follows the required template. The acceptance-gate check for each operation
(the nine standing questions: meaning, need, observable, feasible, verifiable, state/time,
concurrency, consistency, unknown) lives separately in
[`c02-review.md`](c02-review.md), so it can be read and re-read as a review pass on its
own rather than being interleaved with the specification prose.

Terms used below without re-definition are defined once, here:

- **Occupied** (from the Project Frame): a seat is occupied for a screening if a reservation
  for that seat and screening exists in state `CONFIRMED`, or in state `DRAFT` with
  `hold_until` not yet passed. This is evaluated lazily at read time — ADR-003's Decision —
  and the row's stored `state` column is never rewritten to `EXPIRED` as a side effect —
  ADR-003's Consequences ("the `EXPIRED` state may be implicit in the table for a while").
- **Hold window**: 15 minutes from `create`, fixed, not configurable per request in this
  baseline.
- **No-orphan rule**: after the operation, no row touched by it may contain a maximal run
  of free seats of length exactly 1, counting both row ends as occupied. See
  `intent-and-change.md` for the worked example.

---

## OP-01 — Create Reservation

**Goal / user value:** let a viewer claim specific seats for a screening so the seats stay
theirs while they decide, without another viewer taking them in the meantime.

**Trigger:** `POST /reservations` with the viewer's email, a `screening_id`, and 1..N
`seat_ids`.

**Observable requirement(s):** the response is either `201 Created` carrying the new
reservation's `id`, `state` (`DRAFT`), and `hold_until`, or one of the documented failure
codes below — never a silent partial success.

**Preconditions:**
- The screening exists and has not yet started (`now < screening.starts_at`) — stated
  directly in `intent-and-change.md`'s Core operations table.
- Every requested seat exists and belongs to the screening's hall.
- `seat_ids` is non-empty and contains no duplicate.
- None of the requested seats is *occupied* (definition above) at the moment of the check.
- Applying the request does not create an orphan (definition above) in any row it touches.

**Success postcondition:** one new `reservations` row, `state = DRAFT`,
`hold_until = now + 15min`; one new `reservation_seats` row per requested seat,
`state = DRAFT`. Both written in one transaction.

**State change:** *(none)* → `DRAFT`.

**Referenced business rule(s) / invariant(s):** Definition of Occupied, including its
exclusivity guarantee — at most one viewer may hold a seat as a live `DRAFT` or as
`CONFIRMED` at any moment (`intent-and-change.md`); no-orphan rule (ADR-006); the
screening-not-started boundary (`intent-and-change.md`, Core operations table). Create
now carries the same class of race-safety requirement confirm has under ADR-004 — see the
Concurrency row in [`c02-review.md`](c02-review.md).

### Main success scenario
1. Viewer picks a screening and 1..N seats, submits the request.
2. System resolves or creates the `users` row for the given email.
3. System confirms the screening exists and has not started.
4. System confirms every seat id belongs to the screening's hall.
5. System computes the occupied/free status of every seat in every row touched by the
   request (definition of Occupied) and rejects if any requested seat is already occupied.
   This check must hold under concurrent requests, not just sequential ones: it is the
   exclusivity guarantee from the Project Frame, not a UX convenience.
6. System evaluates the no-orphan rule per touched row, treating the requested seats as
   newly occupied.
7. System inserts the reservation (`DRAFT`, `hold_until = now + 15min`) and one
   `reservation_seats` row per seat, in one transaction.
8. System returns `201` with the reservation id, state, and `hold_until`.

### Alternative / failure outcomes
- Unknown `screening_id` → `404`.
- Screening already started → `409` (`screening_already_started`).
- Unknown seat id, or seat not in the screening's hall → `404`.
- Empty or duplicate `seat_ids` → `422` (`invalid_seat_selection`).
- A requested seat is occupied → `409` (`seat_taken`, names the seat).
- Two concurrent create requests target the same seat → exactly one `201`, the other
  `409 seat_taken` — required, not merely likely (see Assumption below for what still needs
  to be built to guarantee this).
- The selection would orphan a seat → `422` (`orphan_seat`, names the row and the seat
  that would be stranded).

### Verification examples
- Given the 8-seat row from `intent-and-change.md` with A1,A2,A7,A8 occupied, requesting
  A3+A4 → `201` (free run A5-A6 has length 2). Requesting A4+A5+A6 → `422 orphan_seat`
  (A3 would be stranded — the third worked example in `intent-and-change.md`).
- Requesting a seat already held as a live `DRAFT` by another user → `409 seat_taken`.
- Requesting a seat for screening 99 which does not exist → `404`.
- now < screening.starts\_at → `409`
- Two `create` requests for the same seat arriving together → exactly one `201`, the other
  `409 seat_taken`; never two `201`s for the same seat — the same shape of outcome the C01
  spike measured for confirm (`evidence-and-evolution.md`, Run 2), now required of create
  too.

### Assumption / unknown / TBD
- **The policy is settled; the mechanism is not yet built.** The Project Frame now states
  plainly that at most one viewer may hold a seat as a live `DRAFT` or as `CONFIRMED` at
  any moment, so create() is required to be race-safe the same way confirm() already is
  (ADR-004) — this is no longer an open product question. What remains open is the
  *implementation*: unlike confirm(), create()'s race-safety has not been built or spiked
  yet. A `SELECT`-then-insert check alone is known to be insufficient, for the same reason
  it was insufficient for confirm (`evidence-and-evolution.md`), but there is no equivalent
  of `uq_confirmed_seat_per_screening` covering live `DRAFT` holds today, and building one is
  less direct than it was for `CONFIRMED`: a static unique index over `state = 'DRAFT'` rows
  would keep blocking a seat forever after its holder's hold expires, since expiry is lazy
  (ADR-003) and nothing rewrites the row to `EXPIRED` on its own. Closing this gap needs
  either an opportunistic expiry step folded into `create`'s own transaction or a different
  mechanism entirely — a design question, not a specification question, and out of scope for
  this document. It is now a required piece of C03's work, not merely a candidate one.

---

## OP-02 — Check Availability

**Goal / user value:** let a viewer (or box office operator) see which seats of a screening
are actually free before choosing, and see the live effect of everyone else's holds.

**Trigger:** `GET /screenings/{screening_id}/availability`.

**Observable requirement(s):** `200` with one entry per seat in the screening's hall
(`hall`, `row_label`, `seat_number`, `status` ∈ {`free`, `occupied`}), or `404` if the
screening does not exist.

**Preconditions:** the screening exists.

**Success postcondition:** *(none — read-only)*.

**State change:** none 

### Main success scenario
1. Client requests availability for a screening id.
2. System loads every seat belonging to that screening's hall.
3. For each seat, system evaluates Occupied against the current time (CONFIRMED, or DRAFT
   with `hold_until` in the future).
4. System returns the full seat map with one `free`/`occupied` entry per seat.

### Alternative / failure outcomes
- Unknown `screening_id` → `404`.

### Verification examples
- Screening with seat A5 `CONFIRMED` and seat A6 `DRAFT` with `hold_until` in the past →
  map reports A5 `occupied`, A6 `free`.
- A freshly seeded screening with no reservations → every seat `free`.

### Rationale / source
README "Definition of 'occupied'"; ADR-003; ADR-004's demotion note.

---

## OP-03 — Confirm Reservation

**Goal / user value:** turn a held seat into a definitively sold seat. Per
`intent-and-change.md`, this is *the* state transition both business rules must hold at.

**Trigger:** `POST /reservations/{reservation_id}/confirm`.

**Observable requirement(s):** `200` with the reservation's id and new state `CONFIRMED`
plus `confirmed_at`, or one of the documented failure codes.

**Preconditions:**
- The reservation exists.
- Its (lazily evaluated) state is `DRAFT` — i.e. stored `state = 'DRAFT'` **and**
  `hold_until` has not passed.

**Success postcondition:** reservation `state = CONFIRMED`, `confirmed_at = now`; every one
of its `reservation_seats` rows `state = CONFIRMED`. No seat in this reservation is left
partially confirmed.

**State change:** `DRAFT` → `CONFIRMED`.

**Referenced business rule(s) / invariant(s):** the mandatory rule ("two confirmed
reservations for the same seat must not overlap in time"), enforced here — and only here —
by `uq_confirmed_seat_per_screening` (ADR-004); ADR-005's requirement that `reservations`
and `reservation_seats` change together, in one transaction; the `NotificationService`
boundary rule ("a failed notification must never fail a confirmed reservation").

### Main success scenario
1. Client requests confirm on a reservation id.
2. System begins a transaction and attempts to move the reservation from `DRAFT`
   (with `hold_until` still in the future) to `CONFIRMED`, atomically with the same
   guard as the read.
3. System writes `CONFIRMED` to every `reservation_seats` row of that reservation in the
   same transaction.
4. The database's `uq_confirmed_seat_per_screening` index is checked at commit. If any of
   this reservation's seats is already `CONFIRMED` for this screening under a different
   reservation, the write fails and the whole transaction is rolled back — no seat of this
   reservation ends up `CONFIRMED`.
5. On commit, system calls `NotificationService.reservation_confirmed(reservation)` and
   ignores its outcome for the purpose of this response.
6. System returns `200` with the confirmed reservation.

### Alternative / failure outcomes
- Unknown `reservation_id` → `404`.
- Reservation is `CANCELLED` or `EXPIRED` (stored or lazily-derived) → `409`
  (`invalid_state`, names the current state).
- Reservation is `DRAFT` but `hold_until` has passed → `409` (`hold_expired`) — this is the
  lazy `EXPIRED` case from ADR-003; nothing is written to the row by this rejection.
- The database rejects the write because a seat in this reservation is already `CONFIRMED`
  elsewhere (`uq_confirmed_seat_per_screening`) → `409` (`seat_taken`). This is the exact
  scenario measured in the C01 spike.
- `NotificationService` call fails or times out → confirm still returns `200`; failure is
  not surfaced to the caller as an error.

### Verification examples
- Reproduce the C01 spike as an acceptance-level check: two `DRAFT` reservations on the
  same seat, both confirmed concurrently → exactly one `200 CONFIRMED`, one
  `409 seat_taken`; never two `200`s (`evidence-and-evolution.md`, Run 2).
- Confirm a reservation whose `hold_until` is one second in the past → `409 hold_expired`.
- Confirm a reservation with two seats where a different reservation has since confirmed
  one of them → `409 seat_taken`, and **neither** of this reservation's seats is left
  `CONFIRMED` (atomicity check).

### Assumption / unknown / TBD
- **Confirming an already-`CONFIRMED` reservation.** Not stated anywhere in C01. We chose
  `409 invalid_state` (confirm is not idempotent in this baseline) over silently returning
  `200` again, because a caller receiving `200` twice for two different physical clicks
  cannot tell a real re-confirmation from a duplicated network request. This is a genuine
  open product question — same category as the Project Frame's own *Unknown* — not
  something C01/C02 evidence resolves either way.

---

## OP-04 — Cancel Reservation

**Goal / user value:** let a viewer give up seats they no longer want (or a box office
operator cancel on request), freeing them for someone else, any time before the screening
starts. Cancel is a state change, not a physical delete — the row and its history stay.

**Trigger:** `POST /reservations/{reservation_id}/cancel`.

**Observable requirement(s):** `200` with the reservation's id, new state `CANCELLED`, and
`cancelled_at`, or one of the documented failure codes.

**Preconditions:**
- The reservation exists.
- Its (lazily evaluated) state is `DRAFT` or `CONFIRMED` (not already `CANCELLED`, and not
  `EXPIRED`).
- `now < screening.starts_at`.

**Success postcondition:** reservation `state = CANCELLED`, `cancelled_at = now`; every
`reservation_seats` row of it `state = CANCELLED`. The row is **not** deleted; `id`,
`user_id`, `created_at`, and (if it had one) `confirmed_at` are retained.

**State change:** `DRAFT` → `CANCELLED`, or `CONFIRMED` → `CANCELLED`.

**Referenced business rule(s) / invariant(s):** Definition of Occupied (a cancelled
reservation's seats stop counting as occupied immediately); the explicit Project Frame
boundary "allowed until the screening starts"; ADR-005's same-transaction requirement for
`reservations` and `reservation_seats`.

### Main success scenario
1. Client requests cancel on a reservation id.
2. System loads the reservation together with its screening's `starts_at`.
3. System confirms the (lazy) state is `DRAFT` or `CONFIRMED`, and that the screening has
   not started.
4. System writes `CANCELLED` + `cancelled_at = now` to the reservation and to every one of
   its `reservation_seats` rows, in one transaction.
5. System returns `200` with the cancelled reservation.

### Alternative / failure outcomes
- Unknown `reservation_id` → `404`.
- Reservation already `CANCELLED` → `409` (`invalid_state`) — see Assumption below.
- Reservation is `EXPIRED` (lazily derived) → `409` (`invalid_state`); the state diagram in
  the README shows no `cancel()` arrow leaving `EXPIRED`, so this is a direct reading of an
  existing diagram, not a new assumption.
- `now >= screening.starts_at` → `409` (`screening_already_started`).

### Verification examples
- Cancel a `CONFIRMED` reservation an hour before its screening → `200`; a subsequent
  Check Availability call on that screening shows its seats `free`.
- Cancel a reservation whose screening's `starts_at` is one second in the past → `409
  screening_already_started`.
- Cancel a reservation that is already `CANCELLED` → `409 invalid_state`.

### Rationale / source
`intent-and-change.md` Core operations table ("allowed until the screening starts");
README state diagram; ADR-005.

### Assumption / unknown / TBD
- **Cancelling an already-`CANCELLED` reservation.** Same open question as double-confirm
  in OP-03, decided the same way (`409`, not idempotent) for the same reason — a client
  cannot distinguish a genuine re-cancel from a retried request if both return `200`. Listed
  as open rather than settled by evidence, exactly like OP-03's twin case.

