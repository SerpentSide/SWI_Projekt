# C02 Review — Requirement Acceptance Gate

The C02 brief asks that the acceptance gate (meaning, need/rationale, observable, feasible,
verifiable, state/time, concurrency, consistency, unknown) be applied to every requirement.
This document is that check, one table per operation, kept separate from
[`operations-specification.md`](operations-specification.md) so the specification itself
stays about *what each operation does* and this stays about *why we believe it is ready*.
Every row is meant to be read against that spec's matching OP-xx slice, not standalone.

## OP-01 — Create Reservation

| Question | Answer |
|---|---|
| Meaning | "Occupied", "orphan", "hold window" all point at single definitions given once in the spec; no ambiguity left in this slice. |
| Need / rationale | Prevents a viewer from holding seats that don't exist, don't belong to the screening, are already gone, or belong to a screening that has already started — the minimum a create must guard. |
| Observable | Yes — HTTP status + body is the whole observable surface; no hidden design prescription. |
| Feasible | Holds together with OP-03 and OP-04: create only ever produces `DRAFT`, which both later operations expect. |
| Verifiable | Each failure path has a concrete request/response pair (see the spec's Verification examples) and an automated test. |
| State / time | Yes — depends on current time twice: the screening-started check and the occupied check's lazy expiry. |
| Concurrency | The *policy* is closed: the Project Frame now states plainly that at most one viewer may hold a seat as a live `DRAFT` or as `CONFIRMED` at any moment, so create() must be race-safe the same way confirm() already is. The *mechanism* to guarantee it is not built yet — see the spec's Assumption note. Left explicit rather than claimed safe. |
| Consistency | Agrees with `intent-and-change.md`'s Core operations row (screening-not-started) and Definition of "occupied" (exclusivity guarantee), and with ADR-006. |
| Unknown? | One item remains open: the mechanism that gives create() the same database-backed guarantee confirm() has (ADR-004) has not been built or spiked. This is now implementation work owed to C03, not an open product question — the policy itself is settled. The screening-started question that was previously an assumption here is likewise settled directly in the Project Frame. |

## OP-02 — Check Availability

| Question | Answer |
|---|---|
| Meaning | Same Occupied definition as OP-01; no new terms introduced. |
| Need / rationale | Without this, a viewer cannot make an informed OP-01 request at all. |
| Observable | Yes — a plain seat list, no internal state exposed. |
| Feasible | Consistent with OP-01/OP-03/OP-04: it reads exactly the state those write. |
| Verifiable | Deterministic given a seeded DB and a frozen clock — see the spec's Verification examples. |
| State / time | Yes, entirely time-dependent (lazy expiry) — repeated calls can legitimately return different answers with no write in between. |
| Concurrency | The response can be stale the instant it is sent; this is intentional and documented (ADR-004) rather than a bug to fix here. |
| Consistency | Agrees with ADR-003 and ADR-004; does not claim to be a lock. |
| Unknown? | None genuinely open for this slice. |

## OP-03 — Confirm Reservation

| Question | Answer |
|---|---|
| Meaning | `DRAFT`, `CONFIRMED`, `hold_until` all trace to the single state diagram in the README; no re-definition here. |
| Need / rationale | This is the operation the entire mandatory business rule exists to protect — see Project Frame. |
| Observable | Yes; the *mechanism* (unique index vs. app-level lock) is deliberately not exposed to the caller, only the outcome is. |
| Feasible | Consistent with OP-01 (only acts on what create produced) and OP-04 (both terminate into disjoint states). |
| Verifiable | Directly re-uses the already-executed, automated C01 spike as its concurrency verification. |
| State / time | Yes — `hold_until` vs. now decides eligibility; re-checked at the moment of confirm, not at create. |
| Concurrency | Closed by a measured database guarantee (ADR-004), not an assumption — the one place in this review where that is true. |
| Consistency | Agrees with `intent-and-change.md`, ADR-004, ADR-005 without contradiction. |
| Unknown? | Double-confirm idempotency stated explicitly as open in the spec, not fabricated. |

## OP-04 — Cancel Reservation

| Question | Answer |
|---|---|
| Meaning | "Cancel ≠ delete" is stated explicitly in the assignment framing and reflected in the postcondition (row retained, state flipped). |
| Need / rationale | Lets a viewer release seats voluntarily and lets a box office act on request — both named stakeholders in `intent-and-change.md`. |
| Observable | Yes; deletion vs. state-flip is an internal choice with an externally observable consequence (the row is later still fetchable, just `CANCELLED`). |
| Feasible | Coexists with OP-01/OP-03: cancel only accepts the two non-terminal states they produce. |
| Verifiable | Each failure path has a concrete example in the spec and an automated test. |
| State / time | Yes, twice: reservation's own state and the screening's `starts_at` boundary. |
| Concurrency | Two concurrent cancels of the same reservation: the second necessarily sees `CANCELLED` already and gets `409`, which is correct and requires no extra mechanism beyond the same-state guard already needed for OP-04's failure path. |
| Consistency | Agrees with the Project Frame's explicit boundary; does not contradict OP-03. |
| Unknown? | Double-cancel idempotency stated explicitly as open in the spec, matching OP-03. |

## Open items carried forward

- OP-01: the policy that create() must be race-safe like confirm() is now settled (Project
  Frame); the mechanism to guarantee it is not built yet — a required piece of C03's work.
- OP-03: whether confirming an already-`CONFIRMED` reservation should be idempotent.
- OP-04: whether cancelling an already-`CANCELLED` reservation should be idempotent.

None of these are silently resolved; each is stated where it belongs in
[`operations-specification.md`](operations-specification.md) and repeated here so a
reviewer can see the full set at a glance.
