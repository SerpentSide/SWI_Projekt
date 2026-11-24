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
and tested. Both baselines are approved by the whole team (4 of 4 sign-offs in `diagrams.md`, confirmed 2026-10-13). Not done in C02: a role check for the box office operator (still open after C03, see the risks in the C03 evidence); `NotificationService` was added later and is covered in C03.

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
| 6 | OP-03 step 5 calls `NotificationService`; **no such seam exists in the code** | implementation gap | **open** at C02 — see below. *Update: implemented in `fed5106` (C03), see C03 Part A.* |
| 7 | Spec said a rejected confirm on an expired hold must not rewrite the row; code writes `EXPIRED` | spec | the brief requires *observable* requirements; the stored column is not observable (reads give `EXPIRED` either way). Spec reworded, test now checks what is observable. *Update (PR #11): the code no longer writes it either; the row stays `DRAFT`.* |
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
   rejected. *Update: implemented in `fed5106` (C03); the spec now names the pending-approval,
   approval and rejection notifications (OP-03 Variant, OP-05). Notifying the box office stays open.*
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
the v0.2 implementation is commit `36b7571` (tag `c02-v0.2`).


---

## C03 — Architecture Evidence

Baseline: v0.2
Part A: [`architecture-and-decisions.md`](architecture-and-decisions.md) (A1–A8), corrected after the notification commit `fed5106` (A2, A3, A6, A7).

Drivers: D1 BR-02 under concurrency; D2 one owner of Reservation state and its two-table sync; D3 pending approval outlives the request; D4 a failing notification must not change the result. (No roles — recorded as a gap.)
Decision question: who may change Reservation state, and where is the confirmation decision made so that BR-02 holds and both tables never diverge?
Alternatives: A — Reservation Management module owns every transition; B — database cascade (composite FK, `ON UPDATE CASCADE`) with the guards left in handlers. B was checked in a small spike (a cascaded `UPDATE` that violates the unique index is undone, `COMMIT` leaves both tables consistent).
Scenario walkthrough: Confirm of a VIP-seat reservation with a concurrent confirm of another reservation on the same seat — table in [`c03-architecture.md`](c03-architecture.md) E3.
ADR: **ADR-008** (accepted: A).

Views (all in [`c03-architecture.md`](c03-architecture.md)):
- domain class — C1
- context — G1
- static architecture — G2
- state ownership — G3
- runtime/deployment — G4
- design sequence — H1
- focused design class — H2

Cross-view issues found/resolved (section I): H1 first had Reservation Management calling the notification — contradicted G2/ADR, the call moved to the API after the command returned; `notify`/`deliver` were missing in H2; `Notification` in the domain model flagged as part of the delivery mechanism.
AS-IS → TO-BE delta: six CHANGE rows (state writes, BR-02 mapping, HTTP vs domain errors, occupied/no-orphan, notify location, DB access), three KEEP, two VERIFY (lost notification after a crash, authorisation of `decide`).
Implementation changes: new `reservations.py`, `availability.py`, `db.py`; `main.py` is now HTTP only; `notify`/`notifier`/`deliver` moved into `notifications.py`; tests changed only in their import/patch targets (`cinema.db.DB_PATH`, `cinema.notifications.notifier`, `cinema.availability`).

Behaviour verification:

| Check | Result | Evidence |
|---|---|---|
| before the change | 64 passed | `pytest` on `main` + `c04` base, 2026-10-12 |
| success path (confirm → CONFIRMED / PENDING_APPROVAL), cancel, approve/reject | pass | `test_c02_operations.py::TestConfirm`, `TestConfirmNeedingApproval`, `TestCancel`, `TestApprove` |
| failure: expired hold, seat conflict, multi-seat atomicity | pass | `test_multi_seat_conflict_leaves_no_seat_confirmed` |
| concurrency | pass | `test_two_simultaneous_confirms_on_one_seat_give_exactly_one_200`, `test_double_booking_spike.py` |
| notification failure never fails the reservation | pass | `test_failed_notification_never_fails_the_reservation` |
| after the change | 68 passed (64 old + 4 new) | `python -m pytest -q` |
| `docs/trace_confirm.py` (Part A trace) | same output as in A3 | run after the change |
| running API | `GET /movies` 200, unknown reservation 404 `{"detail": "Reservation not found"}` | `curl` against the reloaded server |

Architecture conformance rule + result:
- Rule: only `reservations.py` (Reservation Management) may write to `reservations` / `reservation_seats` (`seed.py`, the demo loader, is the one declared exception); Reservation Management imports neither FastAPI nor `cinema.main` nor `cinema.notifications`.
- Check: `tests/test_architecture.py` (text scan of `src/cinema/*.py`; `test_the_check_detects_a_violation` shows it can fail; also run once against the real tree with a stray `UPDATE reservations` appended to `main.py` — the test failed, then the line was removed).
- Result: passes. A state change outside a transaction raises (`test_a_state_change_outside_a_transaction_is_refused`), which answers Part A8.

Remaining uncertainty / risk:
- The rule is a text scan: it would not see SQL built from string pieces. A database-level guard (alternative B's cascade) is recorded in ADR-008 as the next step.
- A crash between `COMMIT` and `notify` loses the notification; no retry.
- No authorisation: anyone can call `/decision` and `POST /movies`; OP-05's `403` is still not implemented.
- The `Notification Service` of the Project Frame is, in the implementation, an outbox in our own database plus the browser; a real provider would need a transactional outbox.

Commit/tag: tag `c03-architecture` = commit `817fdd2` (docs `14a7a5e`, code `2c47953`, evidence `817fdd2`); merged to `main` by PR #8 as `6712074`. The 68 tests above were run on this code.

Follow-up after the C03 tag (PR #11, branch `pre-c04-fixes` — named after the exercise number, see "C04" below):
- `reservations.confirm` no longer writes `EXPIRED` when the hold has passed: it answers `409` and the row stays `DRAFT`, which reads as `EXPIRED` and occupies nothing. This is what the state diagram (`diagrams.md`: "derived, not stored"), C1, G3 and I of `c03-architecture.md` and ADR-003 already said; only the code stored it. Nothing observable changes (C02 mismatch 7: the stored column is not observable).
- `/auth/login` uses `INSERT OR IGNORE` instead of `ON CONFLICT … DO NOTHING`, so the API also runs on Python builds whose bundled SQLite is older than 3.24. On such a build (Python 3.11.8, SQLite 3.19.3) 51 of the 68 tests failed before, including the walking skeleton.
- `docs/trace_confirm.py` set `cinema.main.DB_PATH`, which `connect`/`reset_db` no longer read after the refactor (they live in `cinema.db`). Run as documented, it wiped the developer database `cinema.sqlite3` and left one reservation `CONFIRMED` with `DRAFT` seats in it. It now points `cinema.db.DB_PATH` at a throw-away file; its output is the same as in A3 and the developer database is untouched (checked with a marker row).
- ADR-007's status records that the frontend exists although the ADR deferred it; the CP1 path stays API-only.
- Verified: 68 passed on SQLite 3.19.3 and on 3.53.1; walking skeleton over HTTP against `uvicorn` on SQLite 3.19.3 (login 200 → `POST /reservations` 201 `DRAFT` → seats occupied → same seats 409 → confirm 200 `CONFIRMED` → notification delivered → cancel 200); confirm after the hold → 409, stored `DRAFT` in both tables, reads `EXPIRED`, seats free.

---

## C04 — no artifact: the assignment was not given

C04 *Quality & Measurement* (quality concern → evaluable criterion → measurement → trade-off/decision) was not
assigned in our run of the course: the fourth exercise continued the C03 architecture assignment. Our branches
are named after the exercise number, not the assignment, which is why `CV4`, `c04`, `c04_finalization` and
`pre-c04-fixes` all hold C03 work or follow-up fixes, and none of them a quality measurement. There is therefore
no C04 artifact in this repository, and C06's Q1–Q3 do not apply.

The closest quality-related material, for orientation only: the selected future pressure (category Q, a
premiere on-sale burst) in [`intent-and-change.md`](intent-and-change.md), and the C01 spike above, which
measured BR-02 under two concurrent confirms and states that the burst itself is unmeasured ("What this did
not answer").

---

## C05 DD Verification Evidence

Detailed Design: [`architecture-and-decisions.md`](architecture-and-decisions.md), "C05 — Detailed Design"
(unit: Reservation lifecycle). References below: R1–R7 = detailed responsibilities (section 2), the contract
of `plan` / `transition` (3.3), decision-table rows #1–#15 (4.2), sequence (4.3).

### Tests derived from DD

Written from the DD **before** the implementation (`lifecycle.py` did not exist yet). Two files, two levels:

- `tests/test_lifecycle.py` — **unit**: the lifecycle policy alone, plain values, no database, no clock.
- `tests/test_c05_transition.py` — **component**: Reservation Management (`reservations.transition` and its
  wrappers) against a real throw-away SQLite file, no HTTP. Every test passes a fixed `at`, so no test depends on
  the wall clock.

| # | DD element / decision | What must hold? | Test level | Test case |
|---|---|---|---|---|
| U1 | interface contract of `plan` (3.3); `TRANSITIONS` = C02 state diagram | For every command × stored state × hold running/passed × screening started/not × needs approval: `plan` returns a value and never raises; it returns a `Transition` exactly for the edges of the C02 state diagram, otherwise `Refused`; a `Transition` never has target `EXPIRED`. | unit | `test_plan_allows_exactly_the_edges_of_the_state_diagram` (exhaustive, 4 × 5 × 2 × 2 × 2 = 160 combinations, oracle = the edges of `diagrams.md` §2 written out in the test) |
| U2 | algorithm branch R4 — confirm + "a seat requires approval" (rows #1, #2) | `needs_approval` → `PENDING_APPROVAL` without timestamp; otherwise `CONFIRMED` with `confirmed_at`. | unit | `test_confirm_chooses_pending_approval_only_when_a_seat_needs_it` |
| U3 | R1 effective state, BR-01 boundaries | `hold_until == at` → `EXPIRED`, one second earlier → `DRAFT`; pending with `starts_at == at` → `EXPIRED`, one second earlier → `PENDING_APPROVAL`; terminal and `CONFIRMED` states pass through unchanged. | unit | `test_effective_state_boundaries_are_half_open` |
| U4 | R3 is separate from R1 (row #12) | A `DRAFT` with a running hold whose screening has started: effective state is still `DRAFT`, but cancel is `Refused("The screening has already started")`. | unit | `test_cancel_of_a_live_draft_after_the_start_is_refused_by_the_start_guard` |
| U5 | failure behaviour — reason names the **effective** state (rows #5, #9; precedence 3.3) | confirm / approve / reject on a pending reservation of a started screening → `Refused("Cannot <command> a EXPIRED reservation")`. | unit | `test_refusal_names_the_derived_state` |
| C1 | state transition through R5 (both tables, timestamp, commit) | approve of a `PENDING_APPROVAL` → returns `CONFIRMED`; `reservations.state` and **all** `reservation_seats.state` are `CONFIRMED`, `confirmed_at = at`; visible from a second connection (committed). | component | `test_approve_writes_both_tables_and_commits` |
| C2 | collaboration plan → `_change_state` → unique index → `_guard_br02` (sequence 4.3, R6) | approve on a seat already `CONFIRMED` by another reservation → `Conflict`; this reservation and all its seats are still `PENDING_APPROVAL`, `confirmed_at` is NULL; the connection is not left in a transaction. | component | `test_approve_that_loses_br02_changes_nothing` |
| C3 | failure behaviour — a refusal writes nothing (R7, contract of `transition`) | cancel of a `CANCELLED` reservation → `Conflict` naming `CANCELLED`; stored state, seats and `cancelled_at` unchanged; no open transaction. | component | `test_refused_command_leaves_the_reservation_untouched` |
| C4 | R1 used by the commands, not only by reads (row #5) | `reservations.confirm` on a pending reservation of a started screening → `Conflict("Cannot confirm a EXPIRED reservation")`; stored state stays `PENDING_APPROVAL` (expiry not written, ADR-003). | component | `test_confirm_of_an_expired_pending_reservation_names_expired` |
| C5 | precondition — unknown reservation (`_load_facts`) | unknown id → `NotFound`, no open transaction. | component | `test_unknown_reservation_is_not_found` |

**Selected level and why — three of the tests:**

*U2 — confirm chooses `PENDING_APPROVAL` only when a seat needs it*
- Selected level: unit.
- Why this level: the decision depends only on one fact (`needs_approval`). With the DD split (3.1) the fact is an
  input of `plan`, so the branch can be tested with two plain values instead of preparing VIP seats in a database
  and going through HTTP as the C02 test does.
- What design decision does this test verify? That the v0.2 variant is a **branch of the policy** (R4) chosen from a
  fact loaded by the command service, not a query hidden inside the decision — i.e. the purity of `plan`.

*U4 — cancel of a live draft after the start*
- Selected level: unit.
- Why this level: the case needs `hold_until > at >= starts_at`, which is awkward to reach through the API (create
  refuses a started screening) but trivial as a `ReservationFacts` value.
- What design decision does this test verify? That R3 (start guard) is a **separate rule** from R1 (effective
  state): merging them into "expired" would make this `DRAFT` read as expired for every other purpose too.

*C2 — approve that loses BR-02 changes nothing*
- Selected level: component (real SQLite, no HTTP).
- Why this level: the outcome is produced by the collaboration of four parts — `plan`, `_change_state`, the unique
  index in the store and `_guard_br02` around `write_tx` — and the veto only exists in a real database. A mock of
  the database would assert our assumption instead of testing it (C03 A3 showed the real behaviour differs from
  the obvious one: `COMMIT` is reached after the failed statement). HTTP adds nothing to this decision.
- What design decision does this test verify? R6 and the **order of the context managers** (4.3): the rollback runs
  before the `Conflict` exists, so nothing of the losing reservation is ever committed.

Not repeated here: HTTP status codes (404/409) and concurrency of two real requests are already covered by the C02
acceptance tests (`test_c02_operations.py`) and the C01 spike; they run unchanged as the regression suite.

**Run before the implementation** (branch `c05-detailed-design`, 2026-10-16, `python -m pytest -q`): both new
files fail at collection with `ModuleNotFoundError: No module named 'cinema.lifecycle'` — the tests are red because
the designed unit does not exist yet, not because of a test mistake. The existing suite without them: 68 passed.

### Unit/component tests executed

After the implementation (`src/cinema/lifecycle.py` new, `src/cinema/reservations.py` refactored), 2026-10-16,
Python 3.14, `python -m pytest -q`:

| Run | Result |
|---|---|
| `tests/test_lifecycle.py` (U1–U5, U5 ×3 commands) | 7 passed |
| `tests/test_c05_transition.py` (C1–C5) | 5 passed — incl. C4, which fails against the pre-C05 code (its reason named `PENDING_APPROVAL`) |
| whole suite (C01 spike, C02 acceptance, C03 architecture, notifications, C05) | **80 passed** (68 before + 12 new) |
| `docs/trace_confirm.py` (C03 Part A trace) | same output as in A3: second confirm `409`, loser `DRAFT` in both tables |
| HTTP smoke (TestClient): confirm / cancel of a `DRAFT` past its hold | `409 {"detail": "Cannot confirm a EXPIRED reservation"}`, `409 {"detail": "Cannot cancel a EXPIRED reservation"}` |

### Design ↔ implementation mismatch

| DD decision | Implemented? | Evidence | Action |
|---|---|---|---|
| 3.1 pure lifecycle policy in its own module; no DB, no clock, refusals as values | YES | `src/cinema/lifecycle.py` imports only `dataclasses`, `enum`; U1–U5 run without a database | keep |
| 3.2 `TRANSITIONS` table = state diagram, terminal states in no row | YES | `lifecycle.TRANSITIONS`; U1 compares all 160 combinations with the diagram's edges | keep |
| 3.2 confirm is **not** start-guarded (follows OP-03 over BR-03's "Applies to") | YES | `Rule(frozenset({"DRAFT"}), False, None)`; U1 (`started` does not matter for confirm) | keep — recorded as a design risk below. *Changed after C05 (spec v0.3): confirm is start-guarded, see "Change after C05" below* |
| 3.3 contract of `plan`: precedence state check → start guard; reasons name the effective state | YES | `lifecycle.plan`; U4, U5 | keep |
| 3.3 contract of `transition`: one command path; `confirm` / `cancel` / `decide` are one-line wrappers; signatures unchanged | YES | `reservations.transition`; API (`main.py`) not changed; C02 acceptance tests pass unchanged | keep |
| 3.2 / 4.1 `_load_facts` reads the facts **inside** `write_tx` | YES | `transition` calls `lifecycle.plan(command, _load_facts(...), at)` inside `with write_tx(db)` | keep |
| 4.1 `_change_state` takes the `Transition`, refuses outside a transaction | YES | `reservations._change_state(db, id, transition, at)`; `test_a_state_change_outside_a_transaction_is_refused` (call updated to the new signature — the only change to an existing test) | keep |
| 4.3 `_guard_br02` outside `write_tx`: rollback before `Conflict` | YES | `transition`; C2 (both tables still `PENDING_APPROVAL`, no open transaction) | keep |
| 4.1 "`get` / `list_for_user` only call `effective_state`" — i.e. reads need only R1's facts | PARTLY | reads build the same `ReservationFacts` as commands (`_facts`), so they also run the `needs_approval` query, which R1 does not use: one extra `SELECT` per reservation in `list_for_user` | keep the code — one helper instead of two fact builders; cost is one indexed query per row of one user's list. DD unchanged; noted here |
| 4.2 rows #3, #14: refusal of an expired `DRAFT` names `EXPIRED` | YES, **observable change** | before: `"Reservation hold has expired"` / `"Only active reservations can be cancelled"`; now: `"Cannot confirm a EXPIRED reservation"` / `"Cannot cancel a EXPIRED reservation"`; still `409` | keep — OP-03 asks the reason to name the current state; the frontend shows `detail` verbatim (`api.ts:72`), see risks |
| ADR-008 / C03 L2: only `reservations.py` writes Reservation state | YES | `lifecycle.py` has no SQL; `test_only_reservation_management_changes_reservation_state` passes | keep |

### Resolution

No design decision had to be reversed. The implementation follows the DD; the one partial match (reads load a
fact they do not need) was decided in favour of the code and recorded instead of changing the diagram after
the fact. Two places where the **code** was changed to follow the DD, not the other way round:

- the 409 reasons now name the derived state (decision-table rows #5, #9; before C05 a pending reservation of a
  started screening was reported as `PENDING_APPROVAL` by confirm and as "the approval has expired" by decide);
- the guards that were spread over `confirm`, `cancel` and `decide` are now one table; `is_expired` (used only
  by those guards) was removed.

### Remaining design risk

- **Confirm after the screening start.** A `DRAFT` created just before `starts_at` can be confirmed up to 15
  minutes after it (OP-03 and the state diagram allow it, BR-03's "Applies to" line suggests not). Needs a product
  decision; the change would be one cell of `TRANSITIONS` plus U1's oracle. *Update: resolved by spec v0.3 —
  "Change after C05: Confirm is start-guarded (v0.3)" below.*
- **User-facing wording.** The frontend shows the API's `detail` text, so a viewer now reads
  "Cannot confirm a EXPIRED reservation" instead of "Reservation hold has expired". Correct by the spec, less
  friendly; a later change could map refusal reasons to UI texts in the frontend.
- **The policy trusts its facts.** `plan` is only as correct as `_load_facts`; that function is covered by the
  component tests C1–C5, not by unit tests. A new fact (e.g. a role for OP-05's `403`) must be added in both.
- **Authorisation of `decide`** remains open (C03); in this design it would be a new fact and a new refusal in
  `plan`, not a new place.


### Commit/tag

Branch `c05-detailed-design` (from `main` at `bb52a6e`). DD, implementation, tests and the evidence above:
commit `f0fb6e1`. Tag `c05-dd` = the commit that adds this section. The 80 tests above were run on
this code.

The tag points to `44967dd`, which is on no branch: that commit was amended after tagging, and `main` holds
`0c810ce` instead. Both have the parent `f0fb6e1` and the same tree (`git diff 44967dd 0c810ce` is empty), so
the tagged content is exactly what is on `main`.

---

## Before C06: our own pre-assessment and Specification Baseline v0.3

Before handing the project to the assessing team we went through the C06 checklist ourselves on `main` at
`9933331` and reproduced every suspected mismatch with a temporary test before changing anything. Each fix
below starts with a test that failed on `9933331` (or, for behaviour that was already right, a test that we
checked fails when the code is broken on purpose).

### Change after C05: Confirm is start-guarded (v0.3)

| | |
|---|---|
| Found | The C05 risk "confirm after the screening start" (above) had a worse case than recorded: a `DRAFT` on a seat that requires approval, confirmed 5 minutes after `starts_at` with its hold still running, answered `200 PENDING_APPROVAL`, queued the notification "Rezervace uložena - čeká na schválení", and then read `EXPIRED` with its seats free — against OP-03's postcondition "its seats stay occupied". On ordinary seats the same request confirmed a seat of a screening already running, which then could not be cancelled (BR-03). |
| Decision | Confirm is refused (`409 "The screening has already started"`) once `now >= starts_at`, like every other command. OP-03 now agrees with BR-01 and BR-03. The rejected alternative — keep the behaviour and document an exception in OP-03 — would have made "a reservation that already reads `EXPIRED`" a success outcome of confirm. |
| Specification | Baseline **v0.3**: OP-03 precondition, failure outcome and verification example; BR-01 and BR-03 "Applies to"; state diagram guards and OP-03 activity in `diagrams.md` (with a v0.3 sign-off list); Project Frame core operations and state list; README. |
| Design | C05 DD: R3, table 3.2 (the confirm cell is now "yes"), the note under it, decision-table rows 1–2 and new row 16; C03 H1 guards. |
| Code | One cell: `lifecycle.TRANSITIONS[Command.CONFIRM].needs_not_started = True`. |
| Tests (red first) | `test_lifecycle.py`: U1's oracle (the diagram edge now has `now < starts_at`), new **U6** `test_confirm_of_a_live_draft_after_the_start_is_refused_by_the_start_guard` (row 16). `test_c02_operations.py`: `TestConfirm::test_live_draft_at_the_start_instant_is_409` (boundary `now == starts_at`), `TestConfirmNeedingApproval::test_vip_confirm_after_start_is_409_and_sends_no_notification`. All four failed on `9933331`. |
| Observable change | Confirm in the last minutes of a hold that outlives the screening start: `200` → `409`. Nothing else. |

### Other mismatches fixed

| # | Found | Verdict | Resolution |
|---|---|---|---|
| 1 | OP-04 requires the cancel response to carry `cancelled_at`; the API returned only `reservation_id` and `state`, and no test looked at `cancelled_at` at all | implementation | the response carries the instant written as `cancelled_at`; `TestCancel::test_response_carries_cancelled_at` (failed before) |
| 2 | OP-01's verification example said `now < starts_at → 409` | spec (typo) | `now >= starts_at → 409` |
| 3 | OP-02 step 3 left `PENDING_APPROVAL` out of "occupied" | spec | step 3 follows the Definition of Occupied |
| 4 | OP-03 step 4 and OP-05 step 3 said the unique index is checked "at commit"; SQLite checks it per statement (C03 Part A3, D1) | spec | "when the seat rows are updated" |
| 5 | `schema.sql` and `test_api.py::test_double_confirm_is_409` said two `DRAFT` holds on one seat are legal — against the Project Frame and BR-02 (one live hold per seat) | comments | both now say that live holds are exclusive through create's write lock and that the index only backs `CONFIRMED` |
| 6 | ADR-003 promised "a single shared query helper"; expiry is evaluated in two places | ADR text | ADR-003 names both places, one per question, and the tests that pin their common boundary |
| 7 | C03 G2/G4/H2 did not know `lifecycle.py` and `transition` (added by C05); G2's catalogue arrow said "reads" though login and `POST /movies` insert | views | updated, with a pointer to C05 DD 3.4 |
| 8 | Untested specified outcomes: unknown viewer → `404` (OP-01), cancel at exactly `starts_at` → `409` (BR-01) | verification gap | `TestCreate::test_unknown_viewer_is_404`, `TestCancel::test_cancel_at_the_exact_start_instant_is_409`. Both passed at once, because the code was right; both fail when the user check is removed or `starts_at <= at` becomes `<` (checked, then reverted) |

### Verification run

`python -m pytest -q` on Python 3.11.8: **86 passed** (80 before + 6 new: U6 and five C02 acceptance tests).
Before the code change the same suite gave 5 failed, 81 passed — the five red-first tests above.

Still open, unchanged and known: OP-05's `403` (no roles), owner checks on cancel, a lost notification after
a crash between commit and notify (C03, C05 risks); and BR-04 after a cancel: a cancel may leave an isolated
free seat (the rule is checked at create only, ADR-006), and from then on every create in that row that does
not take the seat is refused — the formal definition ("no row touched may *contain* a run of length 1") says
so, the rule's one-line wording ("must not *leave*") reads more narrowly.

## After v0.3: roles (PR #15, `3b8f4bf`)

PR #15 added the user roles `VIEWER`, `BOX_OFFICE`, `ADMIN` (`src/cinema/roles.py`, `users.role`, the
`X-User-Id` header) and the demo-data top-up. OP-05's `403` is therefore implemented and tested
(`TestApprove::test_a_viewer_cannot_decide_403`, `test_lifecycle.py` U7); the "Still open" line above about
`403` is resolved. Still open: the owner check on confirm/cancel (a viewer can confirm or cancel someone
else's reservation) and that login is by e-mail only. `3b8f4bf` with `python -m pytest -q` → **97 passed**
(re-run 2026-11-24, Python 3.13.18, SQLite 3.53.1). This commit is the before baseline of CR-C08.

---

## C08 - Before baseline and TC-C08-01/02

The Change Card is "C08 / CR-C08 Pending approval limit" in [`intent-and-change.md`](intent-and-change.md#c08--cr-c08-pending-approval-limit--controlled-change-card).

| | |
|---|---|
| Baseline | `main` @ `3b8f4bf`; spec v0.3; schema `src/cinema/schema.sql` @ `3b8f4bf` (no `pending_until`); runtime Python 3.13.18, SQLite 3.53.1, Linux; test DB = a fresh SQLite file per test in pytest's `tmp_path` (isolated); no secrets |
| Interval / time semantics | Seat exclusivity per `(screening, seat)` (ADR-004); all instants ISO-8601 UTC; the server clock is `cinema.main.now`, replaced in these tests by a controllable clock starting at `T0 = 2030-01-01T17:00:00Z`; the screening starts at `T0 + 3 h` so the start guard never decides |
| Command | `python -m pytest -v tests/test_c09_pending_limit.py -k tc_c08` |
| Persisted-state oracle | `committed(rid)` and `double_confirmed_seats()` read through a **new** SQLite connection (sees committed data only), independent of the API response |

### C08 / TC-C08-01 — approve next to a confirmed seat

Test basis: OP-05 (approve), BR-02, spec v0.3. Preconditions: hall 1, own screening, first row; ordinary
seats; seats 3–4 of the row require approval; box office user from `seed.sql`.

| # | Action / input | Expected BEFORE execution | Actual AFTER execution (`3b8f4bf` and `5984d13`) | Evidence |
|---|---|---|---|---|
| 1 | At `T0` viewer b1 creates + confirms seats 1–2 | `200 CONFIRMED` | `200 CONFIRMED` | test asserts |
| 2 | At `T0` viewer b2 creates + confirms VIP seats 3–4 (adjacent, no shared seat) | `200 PENDING_APPROVAL`; committed `PENDING_APPROVAL` on both seat rows | as expected | `committed(b2)` |
| 3 | At `T0 + 10:00` box office approves b2 | `200 CONFIRMED` | `200 CONFIRMED` | response |
| 4 | New connection: read b1, b2 and duplicate CONFIRMED seats | b1 `CONFIRMED`; b2 `CONFIRMED`, `confirmed_at = T0+10:00`, both seats `CONFIRMED`; no seat confirmed twice | as expected | `committed`, `double_confirmed_seats() == []` |
| 5 | Repeat | Each test builds a fresh DB file → same result | same result on every run (4 runs on 2026-11-24) | pytest |

Verdict: **PASS** on `3b8f4bf` ([red-first log](evidence/c09/red-first-before-3b8f4bf.txt)) and on `5984d13`
([clean-clone log](evidence/c09/check-clean-clone-5984d13.log)). Not covered: two concurrent approvals (that is
`TestApprove::test_approve_and_cancel_arriving_together_always_end_cancelled` and the C05 C2 test).

### C08 / TC-C08-02 — approve onto an already confirmed seat

Test basis: OP-05 failure outcome "a seat is already `CONFIRMED` elsewhere → 409, nothing confirmed", BR-02,
ADR-004. Preconditions: b2 `PENDING_APPROVAL` on VIP seats X, Y (made through the API at `T0`); then b1
`CONFIRMED` on X, inserted directly (test setup: create would refuse X while b2 holds it).

| # | Action / input | Expected BEFORE execution | Actual AFTER execution (`3b8f4bf` and `5984d13`) | Evidence |
|---|---|---|---|---|
| 1 | Prepare b2 (API) and b1 (direct insert) | only b1 is `CONFIRMED`; b2 pending | as expected | fixture |
| 2 | At `T0 + 10:00` box office approves b2 | `409` (the unique index vetoes) | `409` | response |
| 3 | New connection: read b1, b2 | b1 unchanged (`CONFIRMED`, `confirmed_at = T0`); b2 `PENDING_APPROVAL` on both seats, `confirmed_at` NULL; no seat confirmed twice | as expected | `committed`, `double_confirmed_seats() == []` |
| 4 | Repeat | fresh DB → same result | same | pytest |

Verdict: **PASS** on `3b8f4bf` and on `5984d13` (same logs).

---

## C09 - Integration, CI and Release Record

**C08 Change Card link and reviewed ACCEPT / NO_CHANGE / REJECT rationale:**
[CR-C08 Pending approval limit](intent-and-change.md#c08--cr-c08-pending-approval-limit--controlled-change-card) —
**ACCEPT option A** (lazy expiry at a stored deadline, ADR-009): meets AC-01..AC-05 with the smallest delta and
no new writer. Owner Š. Adámek. _Peer review of the decision: PLACEHOLDER (see below)._

**Before commit/tag; after commit/tag:** before = `3b8f4bf` (`main`, PR #15). After = branch `c89`:
`f11d937` (tests only, red first) → **`5984d13` (implementation, the release candidate code)** → the
documentation commit on top of it (this file; no code change). Suggested tag after review: `c09-rc1` on the
merge commit. Tree of `5984d13`: `2e8327e8a24ffd8a9a06d5cf881fc907ff1ce2e5`.

**Affected claims and new AC, unchanged regression basis:** new AC-01..AC-05 (Change Card); changed claim:
OP-05's verification example "stays `PENDING_APPROVAL` for hours" → "past the 15-minute hold, at most 30
minutes". Unchanged: OP-01..OP-04, roles, BR-01..BR-04, notifications; TC-C08-01/02; all 97 tests of `3b8f4bf`.

**Changed code/data/config/ADR and relevant N/A:**

| Area | Change |
|---|---|
| `src/cinema/schema.sql` | `reservations.pending_until TEXT` (nullable) |
| `src/cinema/db.py` | `add_pending_until()` — idempotent `ALTER TABLE` for older databases; called at startup in `main.py` |
| `src/cinema/reservations.py` | `APPROVAL_LIMIT = 30 min`; `_change_state` writes `pending_until = at + 30 min` when the target is `PENDING_APPROVAL`; `_facts` passes it on |
| `src/cinema/lifecycle.py` | `ReservationFacts.pending_until`; `effective_state`: pending with `pending_until <= at` or NULL → `EXPIRED` |
| `src/cinema/availability.py` | "occupied" for pending also requires `pending_until > now` |
| `src/frontend/.../seat-map-modal.tsx` | the pending message states the 30-minute limit |
| Tests | new `tests/test_c09_pending_limit.py` (10); `test_lifecycle.py` U1 oracle + `in_time` dimension (640 → 1280 combinations), new U8; plant helpers in `test_c02_operations.py` / `test_c05_transition.py` write a deadline for pending rows (otherwise those tests would pass for the wrong reason: a NULL deadline now means expired) |
| CI | new `scripts/check.sh`, `.github/workflows/ci.yml` |
| Spec / design | spec v0.4 (OP-02, OP-03, OP-05, Definition of Occupied), `diagrams.md` v0.4 + sign-off list, ADR-009, ADR-003 note, C05 DD R1 / pseudocode / decision-table row 17, Project Frame, README |
| N/A | no new endpoint, no new env var (limit is a constant — ADR-009), no worker/import/admin writer exists, notifications unchanged (nothing is sent at expiry), no change to roles |

**Peer review: reviewer, challenged assumption, decision:** _PLACEHOLDER — NOT DONE YET. A second team
member reviews the PR diff against AC-01..AC-05 (normal path, the exact 30:00 boundary, rejection path,
persistence, the legacy-row policy, approve/expiry race) and records here: name, date, the assumption they
challenged, and the disposition (approved / changes requested)._ Points the author asks the reviewer to
challenge: (1) is "30:00 is already late" the behaviour the box office wants; (2) fail-closed for legacy
pending rows on upgrade; (3) a request confirmed less than 30 min before the start still expires at the start.

| Test ID | Accepted oracle | Exact command/data | Expected | Actual | PASS/FAIL/BLOCKED | Evidence |
|---|---|---|---|---|---|---|
| TC-C09-01 New: deadline recorded (AC-05) | Change Card AC-05 | `pytest tests/test_c09_pending_limit.py::test_tc_c09_01_confirm_records_the_approval_deadline`; VIP seats, confirm at `T0` | `pending_until = T0+30:00` in GET and in the DB | `2030-01-01T17:30:00Z` in both | PASS (FAIL on `3b8f4bf`: `KeyError 'pending_until'`) | [clean-clone log](evidence/c09/check-clean-clone-5984d13.log), [red-first](evidence/c09/red-first-before-3b8f4bf.txt) |
| TC-C09-02 New: 29:59 (AC-01) | AC-01 | `::test_tc_c09_02_approve_at_29_59_confirms`; approve at `T0+29:59` | `200 CONFIRMED`; committed `CONFIRMED` both tables, `confirmed_at = T0+29:59` | as expected | PASS (also on `3b8f4bf`) | same |
| TC-C09-03 New: 30:00 / 30:01 (AC-03, AC-02) | AC-02, AC-03 | `::test_tc_c09_03_...[30:00]`, `[30:01]` | `409 "Cannot approve a EXPIRED reservation"`; stored row stays `PENDING_APPROVAL`, `confirmed_at` NULL; GET `EXPIRED`; seats free; no double CONFIRMED | as expected | PASS (FAIL on `3b8f4bf`: `200`) | same |
| TC-C09-04 New: reject/cancel after limit | BR-03 + AC-02 | `::test_tc_c09_04_...`; at `T0+30:00` | both `409`; stored `PENDING_APPROVAL` | as expected | PASS (FAIL on `3b8f4bf`) | same |
| TC-C09-05 Regression: pending outlives the 15-min hold | OP-05 v0.2/v0.4 | `::test_tc_c09_05_...`; at `T0+20:00` | reads `PENDING_APPROVAL`, seats occupied, approve `200` | as expected | PASS (also on `3b8f4bf`) | same |
| TC-C09-06 New/regression: BR-02 after expiry (AC-04) | AC-04, BR-02 | `::test_tc_c09_06_...`; second viewer at `T0+31:00` | newcomer `CONFIRMED`; late approve `409`; old row not CONFIRMED; no double CONFIRMED | as expected | PASS (FAIL on `3b8f4bf`: create `409`, seats still held) | same |
| TC-C09-07 New: upgrade of a before-DB | Change Card data row | `::test_tc_c09_07_...`; DB without the column + a legacy pending row | startup adds the column; 2nd call `False`; legacy row reads `EXPIRED`; approve `409` | as expected | PASS | same |
| C08 regression TC-01 | TC-C08-01 above | `-k tc_c08_01` | both `CONFIRMED`, no overlap | as expected | PASS (on `3b8f4bf` and `5984d13`) | same |
| C08 regression TC-02 | TC-C08-02 above | `-k tc_c08_02` | `409`, nothing written | as expected | PASS (on `3b8f4bf` and `5984d13`) | same |
| Full regression suite | specs v0.3/v0.4 | `./scripts/check.sh` | all pass | **108 passed** (97 before + 10 in `test_c09_pending_limit.py` + U8) | PASS | same |
| Unit: lifecycle oracle U1 + U8 | state diagram v0.4 | `pytest tests/test_lifecycle.py` | 1280/1280 combinations as the diagram; 30:00 expired, NULL expired | as expected | PASS | same |
| Smoke on a real server + before-DB | Change Card data row | [smoke-migration.txt](evidence/c09/smoke-migration.txt), [smoke.py](evidence/c09/smoke.py) | column added at startup; confirm writes `pending_until = now+30 min`; after the deadline GET `EXPIRED`, approve `409`, row not CONFIRMED | as expected (`10:03:24Z` → `10:33:24Z`) | PASS | smoke log |
| Script fails when the code is wrong | — | limit set to 31 min in a scratch copy, `SKIP_FRONTEND=1 ./scripts/check.sh` | exit ≠ 0 | exit 1, 4 failed | PASS | [mutation-check.txt](evidence/c09/mutation-check.txt) |
| Approve vs expiry race at the exact instant | — | — | — | — | NOT RUN — both are decided inside one `BEGIN IMMEDIATE` transaction with one `at`, so there is no separate expiry write to race with; not tested by a dedicated test | — |
| Windows run (`start.ps1`) | R-01 | — | — | — | NOT RUN (no Windows machine in this run) | — |

**Real integration boundary and post-commit state check:** FastAPI `TestClient` → `cinema.main` →
`reservations` → a real SQLite file (WAL, `BEGIN IMMEDIATE`, the real unique index); no mocked repository. Only
the clock is replaced. Every state verdict is read through a new connection (`committed()`,
`double_confirmed_seats()`), so it sees committed data only. In addition the smoke check ran a real `uvicorn`
server over HTTP against a database created by the before code.

**Dependency lock + runtime + DB schema + nonsecret config:** Python 3.13.18 (project requires ≥ 3.11),
SQLite 3.53.1, Node v22.23.5 / npm 10.9.9. Backend dependencies are **not pinned** except `pytest==8.3.4`
(`requirements.txt` says `fastapi[standard]>=0.115`); the versions resolved in this run, recorded by the
script in `build/pip-freeze.txt`: fastapi 0.165.0, starlette 1.10.0, pydantic 2.14.4, uvicorn 0.54.0, httpx
0.28.1. Frontend: `src/frontend/package-lock.json` (sha256 `1839780e…0b94`; vite 8.3.0, typescript 6.0.3).
Schema: `src/cinema/schema.sql` sha256 `a94e7fea…3f73` + startup upgrades `add_roles`, `add_pending_until`.
Config: `CINEMA_DB` (path of the SQLite file, optional); no secrets exist.

**Clean checkout/build/test/run commands and environment:** see README "Running what exists today" and
"Clean checkout". In short: `git clone … && git checkout c89 && ./scripts/check.sh`, then
`./.venv/bin/uvicorn cinema.main:app --app-dir src` and `GET /health`.

**CI job or automated local script, observed result/log link:** `scripts/check.sh` (one command: venv +
dependencies, all backend tests with JUnit report, `npm ci` + `npm run build`). Run by the author on a
**fresh clone of `5984d13`** on 2026-11-24 (Linux, Python 3.13.18): exit 0, 108 passed, frontend built —
[log](evidence/c09/check-clean-clone-5984d13.log). GitHub Actions workflow `.github/workflows/ci.yml` runs the
same script on every push and PR. **CI: NOT RUN yet** — the branch has to be pushed to GitHub first; record
the run URL and its result here after the first run.

**Independent reproducer, steps, actual result and any hidden fix:** _PLACEHOLDER — NOT DONE YET. A second
team member (not the author) clones the repository into a new directory, checks out `c89` (or the merge
commit), follows only README "Clean checkout", and records here: name, date, OS, Python/Node version, the
exact commands, the observed result (expected: exit 0, 108 passed, frontend built, `/health` ok), and any
step that needed an undocumented fix. The author's own fresh-clone run above does **not** count as this step._

**Artifact identity/file/hash/tag and migration assumptions:** the product is run from source, so its
identity is the commit + tree: `5984d13`, tree `2e8327e8a24ffd8a9a06d5cf881fc907ff1ce2e5`;
`git archive --format=tar.gz 5984d13 | sha256sum` = `9cfa16fa28dd21d60ecb25c7e66c0f11dcd102f3b252e5a8fbc42e2d2ee51bf2`
(git 2.x on Linux). The frontend bundle is built by `npm run build` into `src/frontend/dist/` (not committed).
Migration: on startup an existing `cinema.sqlite3` gains `pending_until` (nullable); no data is rewritten.

**Recovery/rollback limitation and remaining risks:** rolling back to `3b8f4bf` keeps working on a migrated
database (the old code ignores the extra column), but data interpretation changes back: pending requests the
new code treated as expired become live again until their screening starts. Rolling forward again expires
every pending row whose `pending_until` is past or NULL. Remaining risks: legacy pending rows expire on
upgrade (demo data has none — the seeder writes only `CONFIRMED`); nobody is notified at expiry; backend
dependencies are not pinned (R-01); the limit is a code constant; owner check on confirm/cancel still missing.

**Release candidate decision READY/CONDITIONAL/HOLD, owner, rationale:** **CONDITIONAL** — owner Š. Adámek,
2026-11-24. All accepted AC and the regression suite pass on a clean clone, through a real SQLite boundary,
with committed-state reads. Conditions before READY: (1) peer review of the diff recorded above;
(2) independent clean checkout by a second member recorded above; (3) the GitHub Actions run green on the
pushed branch; (4) team sign-off of spec v0.4 in `diagrams.md`. If any of them fails, the decision becomes HOLD.
