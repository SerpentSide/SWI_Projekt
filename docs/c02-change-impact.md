# C02 Change: approval before confirmation

**Change card.** Some Resources require approval by an authorised person before a
Reservation may become `CONFIRMED`. The approval may be delayed, rejected, or expire.

Baseline v0.1 = the four-operation specification at commit `1a7e2e6`.
Baseline v0.2 = [`operations-specification.md`](operations-specification.md) as changed below.

Team decisions this analysis rests on:

1. **Which Resources need approval.** A fixed property of the `Seat` (`requires_approval`,
   e.g. a VIP row). A reservation needs approval if **at least one** of its seats does.
   (Our Resource is the Seat — ADR-001 — so the flag sits where the brief's "some Resources"
   points.)
2. **Who approves.** The existing **box office operator** role. No new actor.
3. **Pending seats stay blocked** until the decision, so nobody can sell them meanwhile.
4. **Expiry** of an undecided approval is the start of the screening. No timeout value is
   invented.

## Impact analysis (done before editing the specification)

| Area | Question | Answer |
|---|---|---|
| Create | Does it change, or still just create a `DRAFT`? | **Unchanged.** Still creates a `DRAFT` with the 15-minute hold. Whether approval is needed is only decided at Confirm. |
| Availability | Does `PENDING_APPROVAL` block the Resource? Why? | **Yes.** The Definition of Occupied gains `PENDING_APPROVAL` while `now < starts_at`. If it did not block, another viewer could take the seat while the operator deliberates and the approval would have nothing to approve. |
| Confirm | Still instant, or split into request + approval? | **Still one operation, now with two outcomes.** No seat needs approval → `CONFIRMED` as before. Otherwise → `PENDING_APPROVAL`. Splitting it would have broken every existing v0.1 example for ordinary seats. |
| Approve | New actor goal / new operation? Who may do it? | **Yes: OP-05 Approve Reservation**, decision `approve` or `reject`, by a box office operator. |
| Cancel | Can `PENDING_APPROVAL` be cancelled? | **Yes**, until the screening starts, like the other live states (BR-03). Frees the seats. |
| State diagram | Which states are needed? | Add **`PENDING_APPROVAL`** and **`REJECTED`**. Undecided-at-start reads as the existing **`EXPIRED`**. New terminal states: `REJECTED`. |
| Use-case diagram | New actor or goal? | New goal **Approve Reservation** for the existing **Box office operator**. No new actor. |
| Verification | How do we check delay, rejection, expiry and their effect on availability? | New examples in OP-05 and OP-03/OP-04: delayed approval keeps seats occupied for hours; reject frees them; approval after `starts_at` → `409` and the reservation reads `EXPIRED`; approve vs cancel race → exactly one wins. |
| Architecture | New driver for persistence, timers, notifications? | **Yes** — see the drivers below. |

## Dopad změny C02

**Changed condition:** a reservation whose seats need approval no longer becomes `CONFIRMED`
at Confirm; it waits in `PENDING_APPROVAL` for a box office operator's decision.

**Affected requirements / parts of the specification**

- **Definition of Occupied** (Project Frame + specification + README): gains `PENDING_APPROVAL`.
- **OP-03 Confirm:** observable requirement, postcondition and state change now have two cases;
  new variant scenario; `PENDING_APPROVAL`/`REJECTED` added to the `409` cases; one new
  verification example; new open item (who is notified).
- **OP-04 Cancel:** `PENDING_APPROVAL` is cancellable; `REJECTED` is not.
- **OP-05 Approve:** new, full slice.
- **BR-02:** note that the index covers `CONFIRMED` only; **BR-03:** cancellable and terminal
  sets updated.
- **Project Frame:** stakeholder line for the box office operator, operations table, seat
  attribute, state list, definition of occupied.
- **Diagrams:** use-case, lifecycle, activity for OP-03/OP-04/OP-05, traceability table.

**Unaffected parts, and why**

| Part | Why it did not change |
|---|---|
| OP-01 Create | Still creates a `DRAFT` with a 15-minute hold; approval is decided at Confirm. Its exclusivity, orphan and started-screening rules are as before. |
| OP-02 Check Availability | The operation, its trigger and its response shape are unchanged; only the Definition of Occupied it reads changed. |
| BR-01 interval semantics | Approval does not touch time windows. It reuses the `now < starts_at` convention. |
| BR-02 exclusive-resource invariant | Still "no two `CONFIRMED` overlap"; Approve enters `CONFIRMED` through the same guarded write. |
| BR-04 no-orphan rule | Evaluated at Create only; the seats are already held when approval is decided, so nothing new can be orphaned. |
| Hold window (15 min) | Applies to `DRAFT` only. A pending approval is deliberately *not* on the hold clock. |
| C01 spike and ADR-004 | The unique index on `CONFIRMED` is untouched and still the authority. |

**New actor / operation:** no new actor (Box office operator). New operation OP-05.

**Changed rules / state meanings:** `PENDING_APPROVAL` (occupied, cancellable, not
`CONFIRMED`); `REJECTED` (terminal, frees seats); `EXPIRED` now has a second cause
(pending approval undecided at `starts_at`); `CONFIRMED` is reachable from two places.

**Use-case diagram:** Approve Reservation added, linked to Box office operator.

**State diagram:** `PENDING_APPROVAL` and `REJECTED` added; `DRAFT → PENDING_APPROVAL`,
`PENDING_APPROVAL → CONFIRMED | REJECTED | CANCELLED | EXPIRED`.

**New verification examples:** see OP-05 and the v0.2 items in OP-03/OP-04.

**Architectural drivers for C03**

1. **Roles and authorisation.** OP-05 needs "is a box office operator", and the application has
   no roles; anyone can act as any user today (also true for who may cancel or confirm).
2. **Exclusivity of `PENDING_APPROVAL` is not backed by a constraint.** The unique index covers
   `CONFIRMED` only; pending seats are protected by the occupied check plus create's
   serialisation. Same shape as the open `DRAFT` gap, now wider.
3. **A long-lived persistent state that is decided by a human, possibly hours or days
   later.** Lazy expiry (ADR-003) copes; a queue of pending items for the operator to work
   through does not exist and needs a read model.
4. **Notifications.** Someone should learn a decision is waiting, and the viewer that it was
   made. `NotificationService` has no such calls yet and does not exist in the code at all.
