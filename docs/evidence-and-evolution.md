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

**Status: Part A (baseline v0.1) is done except team sign-off. Part B (the approval change, baseline
v0.2) has not been started** — its sections below say so instead of inventing content.

## Accepted baseline

**v0.1** = [`operations-specification.md`](operations-specification.md) (OP-01..OP-04, BR-01..BR-04),
[`c02-review.md`](c02-review.md) (acceptance gate per operation) and [`diagrams.md`](diagrams.md)
(use cases, lifecycle, activity per operation).

**Not yet approved by the team** — the sign-off checklist is at the top of `diagrams.md`. Until it is
ticked this is a draft baseline, not an accepted one.

**v0.2:** does not exist.

## Demonstrated operations

All four run through the real HTTP API (FastAPI + SQLite, `src/cinema/main.py`) and are exercised by
`tests/test_c02_operations.py`:

| Operation | Success examples | Negative / boundary examples |
|---|---|---|
| OP-01 Create | valid 2-seat request → `DRAFT`, hold ≈ 15 min; spec's 8-seat example (adapted to 10) | started screening 409 · duplicate ids 422 · empty 422 · other hall 404 · live foreign hold 409 · orphan 422 · **two simultaneous creates → exactly one 201 (15 rounds)** |
| OP-02 Availability | fresh screening all free; live `DRAFT` occupies | unknown 404 · expired `DRAFT` free · **`hold_until` boundary exclusive at −1 s / 0 s / +1 s (spec timing table)** · read-only |
| OP-03 Confirm | `DRAFT` → `CONFIRMED`, reservation and seats together, `confirmed_at` set | unknown 404 · already confirmed 409 · cancelled 409 · expired 409 · multi-seat conflict confirms nothing · **two simultaneous confirms → exactly one 200 (15 rounds)** |
| OP-04 Cancel | `DRAFT` and `CONFIRMED` before start → `CANCELLED`, row kept, seats free again | unknown 404 · already cancelled 409 · expired 409 · `CONFIRMED` after start 409 · `DRAFT` after start 409 |

## Verification actually run

`python -m pytest tests -q` → **38 passed, 1 xfailed** (31 new C02 tests, 6 `test_api.py`, 2 C01 spike).
Nothing here is a recorded transcript from another machine; it was run on the commit below.

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

### Correction to OP-01

The C02 spec calls create's race-safety unbuilt and "a required piece of C03's work". Measured
behaviour contradicts the first half: create *is* serialised. But the mechanism is the SQLite
single-writer lock, **not** a declarative constraint like `uq_confirmed_seat_per_screening` — nothing in
the schema stops two `DRAFT` holds on one seat if a writer ever bypasses `write_tx`. So the guarantee is
proven for the current stack and remains a C03 concern in a different form (see drivers).

## Open items (not silently decided)

Each is encoded as a test or listed here so it cannot be forgotten:

1. **Does a rejected confirm on an expired hold write `EXPIRED` to the row?** Spec and ADR-003: no. Code:
   yes (it commits `EXPIRED` before answering 409). Test
   `TestConfirm::test_expired_hold_rejection_writes_nothing` is `xfail(strict=True)` until the team picks
   one; when it does, the test or the code flips and the xfail must be removed.
2. **Trigger of Create.** Spec: email in the request. API: `user_id`, obtained from `POST /auth/login`.
3. **Machine-readable error codes** (`seat_taken`, `orphan_seat`, …) promised by the spec are not
   returned; the API returns HTTP status plus a text `detail`. The frontend shows that text.
4. **NotificationService** (mismatch 6): not implemented, not even as a stub.
5. Double-confirm and double-cancel are non-idempotent (`409`) — decided in the spec, implemented and
   tested; still a product question, unchanged.

## Change impact summary

**Not done.** No impact analysis, no `Approve Reservation` slice, no v0.2, and the application was not
changed for it. The only changes made in C02 so far are fixes 1–3 and 5 above, which bring the code in line
with v0.1 rather than implement a change.

## Architectural drivers carried into C03

Only ones with evidence behind them:

1. **create/confirm correctness rests on SQLite's global write lock.** It works and was measured under
   concurrent create and confirm, but it is one lock for the whole database, not per seat, and no
   constraint backs the `DRAFT` side. Any move to concurrent writers needs an equivalent guarantee.
2. **Lazy expiry has no cleanup.** Expired `DRAFT` rows stay, keep their seat rows, and are re-evaluated
   on every read; open item 1 is the same design seam.
3. **No boundary seam for notifications** — the one external system in the Project Frame has no place in
   the code.
4. **HTTP handling, business rules and SQL share one module** (`main.py`), which is why rules such as
   the no-orphan check exist twice (Python and the frontend copy in `seating.ts`).

## Commit

Application, tests and diagrams: `0520489` on branch `c02-evidence`.
