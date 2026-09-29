# C01 Engineering Spike

**Variant: A -- Persistence**
**Executed:** 2026-09-15 · SQLite 3.53.4 (Python `sqlite3` stdlib module) · Python 3.14.7
**Code:** [`tests/test_double_booking_spike.py`](../tests/test_double_booking_spike.py),
schema in [`src/cinema/schema.sql`](../src/cinema/schema.sql)

**Note on history.** This spike was originally executed against PostgreSQL 16 before the
team switched the stack to SQLite (ADR-002). It was re-run against SQLite rather than
just rewritten, because the question below is a question about the *database's* behaviour,
and SQLite's concurrency model is different enough from PostgreSQL's that the old transcript
would no longer be evidence for anything -- it would be a guess wearing the old numbers.

## Question / unknown

When two viewers confirm the **same seat for the same screening at the same moment**,
does SQLite stop the second one -- or does our application code have to?

We did not know this. Our availability check was going to be an ordinary
`SELECT ... WHERE the seat is not confirmed` executed just before the write, and we could
not tell from reading alone whether that check survives two requests arriving together.
Given the future pressure we selected (Q -- a premiere on-sale burst, see
`intent-and-change.md`), this is the question that decides where correctness lives.

The sub-question underneath it: is a partial unique index on
`(screening_id, seat_id) WHERE state = 'CONFIRMED'` enough, or do we need explicit
pessimistic locking in the application?

A second sub-question came from the stack switch itself: SQLite allows only **one writer
at a time** for the whole database file, unlike PostgreSQL's MVCC, where two writers can
genuinely hold conflicting uncommitted writes at once. Does that difference change the
answer, or just the mechanism by which the answer is reached?

## What we did

We built the reservation schema against a real SQLite database file -- not an in-memory
fake, because the behaviour we were asking about *is* the database's behaviour. WAL mode
and a 10-second busy timeout were enabled so that a blocked writer waits instead of
immediately failing with `database is locked`, which is the closer analogue of how a real
deployment would be configured.

We seeded one screening and let **two different users each hold seat A5 as `DRAFT`**, which
our model permits. Then two threads on two separate connections each ran the confirm path:

1. `SELECT` to check whether A5 is already `CONFIRMED` -- the naive application guard;
2. wait on a `threading.Barrier`, so that **both threads finish reading before either
   writes** -- this forces exactly the interleaving a traffic burst produces, instead of
   hoping to hit it by luck;
3. `BEGIN IMMEDIATE`, `UPDATE` the reservation and its seat rows to `CONFIRMED`, then commit.

We ran that identical scenario twice, changing exactly one thing:

- **Run 1** with the partial unique index dropped;
- **Run 2** with the index in place.

Both runs are kept as automated tests, so the finding is re-checkable rather than a story.

## Observed result

```
SQLite: 3.53.4 (Python sqlite3 module)

RUN 1 -- partial unique index DROPPED
  outcome reservation 1 : CONFIRMED
  outcome reservation 2 : CONFIRMED
  CONFIRMED rows for (screening 1, seat A5): 2  <-- seat sold twice

RUN 2 -- partial unique index IN PLACE
  outcome reservation 1 : REJECTED_BY_DB
  outcome reservation 2 : CONFIRMED
  CONFIRMED rows for (screening 1, seat A5): 1  <-- seat sold once
```

```
$ .venv/bin/python -m pytest tests/test_double_booking_spike.py -v

tests/test_double_booking_spike.py::test_without_db_constraint_the_seat_is_sold_twice PASSED
tests/test_double_booking_spike.py::test_with_db_constraint_the_seat_is_sold_once     PASSED

============================== 2 passed in 0.03s ===============================
```

Four things came out of this -- three carried over from the PostgreSQL run, one new:

1. **Without the index, the seat really is sold twice.** Two rows, both `CONFIRMED`,
   same seat, same screening. Our common business rule is violated and nothing anywhere
   notices. Both threads' `SELECT` runs before either thread's `BEGIN IMMEDIATE`, so both
   legitimately observe the seat as free -- SQLite's single-writer lock only starts to
   matter once a thread reaches its write, by which point the wrong decision is already made.

2. **The application-level check never fired at all.** We instrumented a distinct
   `REJECTED_BY_APP` outcome for "the `SELECT` saw the seat taken". It was returned
   **zero times in either run**. So the `SELECT`-then-write guard is not merely unreliable
   under concurrency -- in this interleaving it is worth nothing. That is a stronger
   result than "it sometimes fails", and it is the reason we stopped treating it as a
   safety mechanism.

3. **The partial unique index is sufficient on its own.** No application-level locking
   read was needed. **Which of the two wins is not deterministic** -- in the run recorded
   above reservation 2 won, in other runs reservation 1 did. Our test asserts the *shape*
   of the outcome (one `CONFIRMED`, one `REJECTED_BY_DB`) rather than which reservation
   wins, because asserting a winner would make the test flaky.

4. **New, specific to SQLite: the losing write blocks, it does not race.** Under
   PostgreSQL's `READ COMMITTED` isolation, both transactions' `UPDATE`s could genuinely be
   in flight at once, and the index caught the second one to *commit*. Under SQLite, only
   one connection can hold the write lock at a time: the loser's `BEGIN IMMEDIATE` simply
   waits for the winner to commit, then executes its own `UPDATE` against the now-current
   data and fails the `UNIQUE constraint` check immediately, before it can commit. The
   *architectural* conclusion is unchanged -- the invariant must live in the database, not
   in a pre-write `SELECT` -- but the mechanism is worth recording: SQLite's serialised
   writes did not make the naive `SELECT`-then-write guard safe, because the unsafe read
   already happened before either write was attempted.

## Decision / what changes because of the result

**The no-double-booking invariant lives in the database, not in the application.** The
partial unique index `uq_confirmed_seat_per_screening` is the authority. Four concrete
consequences:

1. **The index ships as part of the schema, not as an optimisation.** It is written into
   [`src/cinema/schema.sql`](../src/cinema/schema.sql) with a comment explaining that it is
   load-bearing, so nobody drops it while tidying up. Recorded as **ADR-004**.

2. **`IntegrityError` (`UNIQUE constraint failed`) becomes an expected, handled outcome --
   not a 500.** The confirm endpoint catches it and returns **HTTP 409 Conflict** with a
   "seat was just taken" message. Losing this race is normal behaviour during a premiere,
   not a defect.

3. **The application-level availability `SELECT` stays, but is demoted.** It is now
   explicitly a *user-experience* feature -- it produces a good seat map and a friendly
   early error -- and is documented as **not** a correctness guarantee. This demotion is
   the main thing that changed in our thinking, and without running the spike we would
   have shipped that `SELECT` believing it was protection.

4. **We did not adopt explicit locking reads.** The result shows SQLite's single-writer
   model plus the index already give us everything a `SELECT ... FOR UPDATE`-style lock
   would; adding one would only add complexity, not correctness. Recorded as **ADR-004**;
   revisit only if a future rule needs to reserve several seats atomically across rows.

### What this did not answer

The spike covered two concurrent requests. It did **not** measure what happens at the
scale our selected pressure actually describes -- several hundred simultaneous confirms
against a handful of hot seats. Because SQLite serialises all writers against one database
file, that scale question is sharper here than it would have been under PostgreSQL: a
premiere burst of confirms across *many different* seats would also queue behind the same
single write lock, even though those seats have no business rule connecting them. Whether
that queueing is fast enough, and whether the write-lock timeout needs tuning, is
unmeasured and is the natural follow-up spike -- it belongs to the work where we actually
implement pressure Q, not to C01.

## How to reproduce

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -v
```

No external service to start -- SQLite is a file, created fresh per test by pytest's
`tmp_path` fixture.

---

# Evidence C02: specification → running application

**Status:** baseline v0.1 and the approval change (baseline v0.2) are specified, drawn, implemented
and tested. What is **not** done: sign-off by the whole team (1 of 4 members has ticked the box),
a role check for the box office operator, and `NotificationService`.

## Accepted baseline

- **v0.1** — four operations (Create, Check Availability, Confirm, Cancel) with BR-01..BR-04, as of
  commit `1a7e2e6`.
- **v0.2** — adds seats that require approval: `PENDING_APPROVAL` / `REJECTED` states, a second
  outcome for Confirm, cancellable `PENDING_APPROVAL`, and OP-05 Approve Reservation.
  Described in [`operations-specification.md`](operations-specification.md),
  [`c02-change-impact.md`](c02-change-impact.md), [`c02-review.md`](c02-review.md) and
  [`diagrams.md`](diagrams.md).


## Demonstrated operations

All five run through the real HTTP API (FastAPI + SQLite, `src/cinema/main.py`) and are exercised by
`tests/test_c02_operations.py`:

| Operation | Success examples | Negative / boundary examples |
|---|---|---|
| OP-01 Create | valid 2-seat request → `DRAFT`, hold ≈ 15 min; spec's row example (adapted to 10 seats) | started screening 409 · duplicate ids 422 · empty 422 · other hall 404 · live foreign hold 409 · orphan 422 · seat held by a pending approval 409 · **two simultaneous creates → exactly one 201 (15 rounds)** |
| OP-02 Availability | fresh screening all free; live `DRAFT` occupies; **pending approval occupies for hours** | unknown 404 · expired `DRAFT` free · **`hold_until` boundary exclusive at −1 s / 0 s / +1 s** · pending of a started screening free · read-only |
| OP-03 Confirm | ordinary seats → `CONFIRMED`; **a VIP seat (even one among ordinary ones) → `PENDING_APPROVAL`** | unknown 404 · already confirmed / cancelled / pending 409 · expired 409 · multi-seat conflict confirms nothing · **two simultaneous confirms → exactly one 200 (15 rounds)** |
| OP-04 Cancel | `DRAFT`, `CONFIRMED` and **`PENDING_APPROVAL`** before start → `CANCELLED`, row kept, seats free | unknown 404 · already cancelled / expired / **rejected** 409 · `CONFIRMED` and `DRAFT` after start 409 |
| OP-05 Approve *(v0.2)* | approve → `CONFIRMED`; reject → `REJECTED` and seats free | deciding twice 409 · unknown 404 · invalid decision 422 · on a `DRAFT` 409 · after `starts_at` 409 and reads `EXPIRED` · collision with a `CONFIRMED` seat 409, nothing confirmed · **approve racing cancel (15 rounds) → always ends `CANCELLED`** |

## Verification actually run

`python -m pytest tests -q` → **57 passed** (49 in `test_c02_operations.py` of which 18 are v0.2, 6 in
`test_api.py`, 2 C01 spike), run three times in a row with the same result. Run on the commit below.

Not run: the non-operator `403` of OP-05 (no roles to test against) — deferred, see Open items.

## Mismatches found and how each was resolved

Rule applied: decide whether the *spec*, the *example* or the *implementation* is wrong, and fix that one.

| # | Found | Verdict | Resolution |
|---|---|---|---|
| 1 | Create accepted a screening that had already started (spec OP-01 + Project Frame: refuse) | implementation | fixed → 409 |
| 2 | Duplicate `seat_ids` silently de-duplicated instead of 422 (spec OP-01) | implementation | fixed → 422 |
| 3 | Cancel checked "screening started" only for `CONFIRMED`; a `DRAFT` could be cancelled after start (spec BR-03 says both) | implementation | fixed → 409 |
| 4 | Spec OP-01 said create's race-safety mechanism "has not been built". It has: create runs inside `BEGIN IMMEDIATE`, the same write lock confirm uses. 15 simultaneous-create rounds gave exactly one 201 every time | spec | see "Correction to OP-01" below |
| 5 | Repo did not run on Windows: `zoneinfo` has no `Europe/Prague` without the `tzdata` package | environment | `tzdata` added to `requirements.txt` for Windows |
| 6 | OP-03 step 5 calls `NotificationService`; **no such seam exists in the code** | implementation gap | **open** — see below |
| 7 | Spec said a rejected confirm on an expired hold must not rewrite the row; code writes `EXPIRED` | spec | the brief requires *observable* requirements; the stored column is not observable (reads give `EXPIRED` either way). Spec reworded, test now checks what is observable |
| 8 | Spec said Create takes the viewer's **email**; API takes `user_id` issued by `/auth/login` | spec | the brief only says "Authorized User"; spec now says logged-in `user_id` |
| 9 | Spec named error codes (`seat_taken`, `orphan_seat`, …) that no code returns | spec | the brief asks for failure *outcomes*, and warns against invented precision; codes removed, status + reason remain |
| 10 | Spec OP-02 said seats carry `status ∈ {free, occupied}`; API returns an `occupied` boolean | spec | same reasoning; spec now says an `occupied` flag |
| 11 | **v0.2 example wrong:** the spec said Approve and Cancel arriving together give "exactly one 200, the other 409". Running it gave two 200s: if Approve commits first, Cancel then legitimately cancels the now-`CONFIRMED` reservation (BR-03 allows it) | example / spec | the code is right. Spec, review and impact analysis now state the real invariant: Cancel is 200 in either order, Approve is 200 or 409, the reservation always ends `CANCELLED`. The test asserts exactly that |

### Correction to OP-01

The C02 spec called create's race-safety unbuilt and "a required piece of C03's work". Measured
behaviour contradicts the first half: create *is* serialised. But the mechanism is the SQLite
single-writer lock, **not** a declarative constraint like `uq_confirmed_seat_per_screening` — nothing in
the schema stops two `DRAFT` holds on one seat if a writer ever bypasses `write_tx`. So the guarantee is
proven for the current stack and remains a C03 concern in a different form (see drivers).

## Open items

1. **Role of the box office operator.** OP-05 requires "a box office operator" and specifies `403`
   for anyone else, but the application has no roles: anyone can act as any user, and Confirm and
   Cancel do not check ownership either. Left for C03 on purpose (a fake header would look like
   protection and not be any).
2. **NotificationService** (mismatch 6): not implemented, not even as a stub, although OP-03 step 5 and
   OP-05 call it. Also unspecified: who is told that a reservation is waiting for a decision, or was
   rejected.
3. Double-confirm, double-cancel and deciding twice are non-idempotent (`409`) — decided in the spec,
   implemented and tested; still a product question, unchanged.
4. **Authorisation in Create.** The brief's reference Create rejects an unauthorised user; ours only
   checks that the `user_id` exists.

## Change impact summary

The change card (approval before confirmation) was analysed **before** editing anything —
[`c02-change-impact.md`](c02-change-impact.md) — and then applied:

- **Changed:** the Definition of Occupied (gains `PENDING_APPROVAL`), OP-03 (two outcomes), OP-04
  (`PENDING_APPROVAL` cancellable), BR-02/BR-03 notes, the Project Frame and README, the use-case and
  lifecycle diagrams, three activity diagrams; **new** OP-05 and two states.
- **Deliberately unchanged, with reasons:** Create, Check Availability's operation and response, BR-01,
  BR-02's invariant, BR-04, the 15-minute hold (pending is not on the hold clock), and the C01
  unique index — Approve enters `CONFIRMED` through the same guarded write.
- **Application:** schema (2 states, `seats.requires_approval`), `POST /reservations/{id}/decision`,
  branching Confirm, pending-aware availability and lazy expiry, a demo VIP row (J) in the seed, and a
  waiting-for-approval message in the seat dialog.
- **Cost of the change**, from the diff of commit `36b7571` (application code only): 4 files, 76 lines
  added and 13 removed, of which `main.py` is 72; no existing v0.1 test needed changing except to pin
  its seats to ordinary (non-VIP) ones.

## Architectural drivers carried into C03

Only ones with evidence behind them:

1. **create/confirm/approve correctness rests on SQLite's global write lock.** It works and was
   measured under concurrent create, confirm and approve-vs-cancel, but it is one lock for the whole
   database, not per seat, and no constraint backs the `DRAFT` and `PENDING_APPROVAL` sides. Any move
   to concurrent writers needs an equivalent guarantee.
2. **No roles or authorisation** — needed by OP-05 (operator), and missing for owner checks too.
3. **Lazy expiry has no cleanup and now has two causes** (hold passed, screening started while
   pending). Expired rows stay and are re-evaluated on every read; a long-lived pending state also
   needs a work queue for the operator, which does not exist.
4. **No boundary seam for notifications** — the one external system in the Project Frame has no place
   in the code, and v0.2 adds two more moments that want a notification.
5. **HTTP handling, business rules and SQL share one module** (`main.py`), which is why rules such as
   the no-orphan check exist twice (Python and the frontend copy in `seating.ts`) and why the
   approval branch had to touch several unrelated functions.

## Commit

Application, tests and specification for v0.1 and v0.2: see `git log` on branch `c02-evidence`;
the v0.2 implementation is commit `36b7571`.
