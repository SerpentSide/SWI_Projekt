# C01 Engineering Spike

**Variant: A -- Persistence**
**Executed:** 2026-09-15 · PostgreSQL 16.15 in Docker · Python 3.11.8 · psycopg 3.2.3
**Code:** [`tests/test_double_booking_spike.py`](../tests/test_double_booking_spike.py),
schema in [`src/cinema/schema.sql`](../src/cinema/schema.sql)

## Question / unknown

When two viewers confirm the **same seat for the same screening at the same moment**,
does PostgreSQL stop the second one -- or does our application code have to?

We did not know this. Our availability check was going to be an ordinary
`SELECT ... WHERE the seat is not confirmed` executed just before the write, and we could
not tell from reading alone whether that check survives two requests arriving together.
Given the future pressure we selected (Q -- a premiere on-sale burst, see
`intent-and-change.md`), this is the question that decides where correctness lives.

The sub-question underneath it: is a partial unique index on
`(screening_id, seat_id) WHERE state = 'CONFIRMED'` enough, or do we need explicit
pessimistic locking (`SELECT ... FOR UPDATE`) in the application?

## What we did

We built the reservation schema against a real PostgreSQL 16 instance in Docker -- not an
in-memory fake, because the behaviour we were asking about *is* the database's behaviour.

We seeded one screening and let **two different users each hold seat A5 as `DRAFT`**, which
our model permits. Then two threads on two separate connections each ran the confirm path:

1. `SELECT` to check whether A5 is already `CONFIRMED` -- the naive application guard;
2. wait on a `threading.Barrier`, so that **both threads finish reading before either
   writes** -- this forces exactly the interleaving a traffic burst produces, instead of
   hoping to hit it by luck;
3. `UPDATE` the reservation and its seat rows to `CONFIRMED`, then commit.

We ran that identical scenario twice, changing exactly one thing:

- **Run 1** with the partial unique index dropped;
- **Run 2** with the index in place.

Both runs are kept as automated tests, so the finding is re-checkable rather than a story.

## Observed result

```
PostgreSQL: PostgreSQL 16.15 on aarch64-unknown-linux-musl

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

============================== 2 passed in 0.26s ===============================
```

Three things came out of this, one of them sharper than we expected:

1. **Without the index, the seat really is sold twice.** Two rows, both `CONFIRMED`,
   same seat, same screening. Our common business rule is violated and nothing anywhere
   notices. Under PostgreSQL's default `READ COMMITTED` isolation, neither transaction can
   see the other's uncommitted write, so both legitimately believe the seat is free.

2. **The application-level check never fired at all.** We instrumented a distinct
   `REJECTED_BY_APP` outcome for "the `SELECT` saw the seat taken". It was returned
   **zero times in either run**. So the `SELECT`-then-write guard is not merely unreliable
   under concurrency -- in this interleaving it is worth nothing. That is a stronger
   result than "it sometimes fails", and it is the reason we stopped treating it as a
   safety mechanism.

3. **The partial unique index is sufficient on its own.** No `SELECT ... FOR UPDATE` was
   needed. The loser's transaction blocked on the index entry until the winner committed
   and then failed with `UniqueViolation`. **Which of the two wins is not deterministic** --
   in the run recorded above reservation 2 won, in other runs reservation 1 did. Our test
   asserts the *shape* of the outcome (one `CONFIRMED`, one `REJECTED_BY_DB`) rather than
   which reservation wins, because asserting a winner would make the test flaky.

## Decision / what changes because of the result

**The no-double-booking invariant lives in the database, not in the application.** The
partial unique index `uq_confirmed_seat_per_screening` is the authority. Four concrete
consequences:

1. **The index ships as part of the schema, not as an optimisation.** It is written into
   [`src/cinema/schema.sql`](../src/cinema/schema.sql) with a comment explaining that it is
   load-bearing, so nobody drops it while tidying up. Recorded as **ADR-004**.

2. **`UniqueViolation` becomes an expected, handled outcome -- not a 500.** The confirm
   endpoint catches it and returns **HTTP 409 Conflict** with a "seat was just taken"
   message. Losing this race is normal behaviour during a premiere, not a defect.

3. **The application-level availability `SELECT` stays, but is demoted.** It is now
   explicitly a *user-experience* feature -- it produces a good seat map and a friendly
   early error -- and is documented as **not** a correctness guarantee. This demotion is
   the main thing that changed in our thinking, and without running the spike we would
   have shipped that `SELECT` believing it was protection.

4. **We did not adopt pessimistic locking.** `SELECT ... FOR UPDATE` was on the table
   before the spike; the result shows it would add contention on the hottest rows during
   exactly the burst we are worried about, while buying nothing the index does not already
   give us. Recorded as **ADR-004**; revisit only if a future rule needs to reserve
   several seats atomically across rows.

### What this did not answer

The spike covered two concurrent requests. It did **not** measure what happens at the
scale our selected pressure actually describes -- several hundred simultaneous confirms
against a handful of hot seats. Index contention and connection-pool exhaustion are
plausible there and remain unmeasured. That is the natural follow-up spike, and it belongs
to the work where we actually implement pressure Q, not to C01.

## How to reproduce

```bash
docker compose up -d                        # PostgreSQL 16 on localhost:55432
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -v
```
