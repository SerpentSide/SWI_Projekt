# C02 Diagrams

Three views of the same minimum system, all drawn from
[`operations-specification.md`](operations-specification.md) (which is authoritative — if a
diagram and the text disagree, the text wins and the diagram is a bug). Mermaid, so GitHub
renders them and they diff as text.

These are the **v0.2** diagrams (approval change, see [`c02-change-impact.md`](c02-change-impact.md)).
Elements added by v0.2 are marked *(v0.2)* in the text and drawn with a dashed line where the
diagram type allows it. The v0.1 diagrams are the same file at commit `1a7e2e6`.

**Baseline status: Specification Baseline v0.1 and v0.2 — awaiting team approval.** Nobody has
signed either off yet; tick the box when you have read the spec, `c02-review.md`,
`c02-change-impact.md` and these diagrams and accept them as the baseline.

- [x] Jan Procházka
- [ ] Jiří Ševeček
- [ ] Šimon Adámek
- [ ] Stanislav Urban

## 1. Use cases — actors and goals

System boundary = the reservation API. Only goals that have a specified operation appear.
The Project Frame also lets a box-office operator "look up a reservation"; that is not one of
the baseline operations and is deliberately left out until it has an OP slice.

`NotificationService` is drawn because it really exists (Project Frame, external boundary),
and only where the spec makes the call: after a reservation becomes `CONFIRMED`, by Confirm
or by Approve.

```mermaid
flowchart LR
    viewer([Viewer])
    operator([Box office operator])
    notif([NotificationService<br/>external, third-party])

    subgraph system [Cinema seat reservation system]
        direction TB
        create((Create<br/>Reservation))
        avail((Check<br/>Availability))
        confirm((Confirm<br/>Reservation))
        cancel((Cancel<br/>Reservation))
        approve((Approve<br/>Reservation<br/>v0.2))
    end

    viewer --- create
    viewer --- avail
    viewer --- confirm
    viewer --- cancel
    operator --- avail
    operator --- cancel
    operator -.- approve
    confirm --- notif
    approve -.- notif
```

| Goal | Actor(s) | Spec |
|---|---|---|
| Create Reservation | Viewer | OP-01 |
| Check Availability | Viewer, Box office operator | OP-02 |
| Confirm Reservation | Viewer (+ NotificationService is called when it ends `CONFIRMED`) | OP-03 |
| Cancel Reservation | Viewer, Box office operator (BR-03) | OP-04 |
| Approve Reservation *(v0.2)* | Box office operator (+ NotificationService on approval) | OP-05 |

## 2. Reservation lifecycle — state diagram

`EXPIRED` is **derived, not stored**: a `DRAFT` whose `hold_until` has passed, or a
`PENDING_APPROVAL` whose screening has started, *reads as* `EXPIRED` (ADR-003). Guards use the
half-open conventions of BR-01: `now < starts_at`, `hold_until > now`.

```mermaid
stateDiagram-v2
    [*] --> DRAFT: create [screening not started, seats free, no orphan]
    DRAFT --> CONFIRMED: confirm [hold_until > now, no seat needs approval, no seat already CONFIRMED elsewhere]
    DRAFT --> PENDING_APPROVAL: confirm [hold_until > now, a seat needs approval] (v0.2)
    PENDING_APPROVAL --> CONFIRMED: approve [now < starts_at, no seat already CONFIRMED elsewhere] (v0.2)
    PENDING_APPROVAL --> REJECTED: reject [now < starts_at] (v0.2)
    DRAFT --> CANCELLED: cancel [now < starts_at]
    PENDING_APPROVAL --> CANCELLED: cancel [now < starts_at] (v0.2)
    CONFIRMED --> CANCELLED: cancel [now < starts_at]
    DRAFT --> EXPIRED: hold_until <= now (derived, no operation)
    PENDING_APPROVAL --> EXPIRED: now >= starts_at (derived, no operation) (v0.2)
    CANCELLED --> [*]
    EXPIRED --> [*]
    REJECTED --> [*]

    note right of PENDING_APPROVAL
        Occupied (seats stay blocked) and not on the
        15-minute hold clock: it can wait until the
        screening starts.
    end note

    note right of EXPIRED
        Terminal. confirm, approve and cancel are all
        rejected (409). Whether the stored row is
        rewritten is not specified (ADR-003).
    end note
```

Rejected requests never change state: every failure path in OP-01..OP-05 leaves the
reservation exactly where it was, so no failure arrows are drawn.

## 3. Activity diagrams — behaviour of each operation

Decision order matches the "Main success scenario" of each OP slice; outcomes are the HTTP
status codes from its "Alternative / failure outcomes" (the spec defines no error-code names).
OP-01 and OP-02 are unchanged by v0.2 (only the definition of "occupied" that OP-01/OP-02 read
gained `PENDING_APPROVAL`).

### OP-01 Create Reservation

```mermaid
flowchart TD
    start([POST /reservations]) --> s{screening exists?}
    s -- no --> e404a[/404/]
    s -- yes --> st{now < starts_at?}
    st -- no --> e409a[/409 screening started/]
    st -- yes --> v{seat ids non-empty<br/>and unique?}
    v -- no --> e422a[/422 invalid selection/]
    v -- yes --> h{all seats exist<br/>in the screening's hall?}
    h -- no --> e404b[/404/]
    h -- yes --> tx[[BEGIN IMMEDIATE - one write transaction]]
    tx --> o{any requested seat occupied?<br/>CONFIRMED, live DRAFT<br/>or PENDING_APPROVAL}
    o -- yes --> e409b[/409 seat taken/]
    o -- no --> or{would leave an isolated<br/>free seat in a touched row?}
    or -- yes --> e422b[/422 orphan seat/]
    or -- no --> ins[INSERT reservation DRAFT, hold_until = now + 15 min<br/>INSERT one reservation_seats row per seat]
    ins --> ok([201 id + hold_until])
```

### OP-02 Check Availability

```mermaid
flowchart TD
    start([GET /screenings/id/availability]) --> s{screening exists?}
    s -- no --> e404[/404/]
    s -- yes --> load[load every seat of the screening's hall]
    load --> loop[for each seat: occupied if CONFIRMED,<br/>or DRAFT with hold_until > now,<br/>or PENDING_APPROVAL with now < starts_at]
    loop --> ok([200 seat map, nothing written])
```

### OP-03 Confirm Reservation *(v0.2: two outcomes)*

```mermaid
flowchart TD
    start([POST /reservations/id/confirm]) --> r{reservation exists?}
    r -- no --> e404[/404/]
    r -- yes --> tx[[BEGIN IMMEDIATE]]
    tx --> d{stored state = DRAFT?}
    d -- no --> e409a[/409 invalid state/]
    d -- yes --> hold{hold_until > now?}
    hold -- no --> e409b[/409 hold expired/]
    hold -- yes --> ap{does any seat<br/>require approval?}
    ap -- yes --> pend[UPDATE reservation AND all its seats<br/>to PENDING_APPROVAL]
    pend --> commitp[COMMIT]
    commitp --> okp([200 PENDING_APPROVAL])
    ap -- no --> upd[UPDATE reservation AND all its seats to CONFIRMED]
    upd --> uq{uq_confirmed_seat_per_screening<br/>violated?}
    uq -- yes --> rb[ROLLBACK - no seat confirmed]
    rb --> e409c[/409 seat taken/]
    uq -- no --> commit[COMMIT]
    commit --> notify[NotificationService.reservation_confirmed<br/>outcome ignored]
    notify --> ok([200 CONFIRMED])
```

### OP-04 Cancel Reservation *(v0.2: also PENDING_APPROVAL)*

```mermaid
flowchart TD
    start([POST /reservations/id/cancel]) --> r{reservation exists?}
    r -- no --> e404[/404/]
    r -- yes --> tx[[BEGIN IMMEDIATE]]
    tx --> d{state is DRAFT, PENDING_APPROVAL<br/>or CONFIRMED, and not expired?}
    d -- no --> e409a[/409 invalid state/]
    d -- yes --> t{now < starts_at?}
    t -- no --> e409b[/409 screening started/]
    t -- yes --> upd[UPDATE reservation AND all its seats to CANCELLED<br/>set cancelled_at - row kept]
    upd --> ok([200 CANCELLED, seats free again])
```

### OP-05 Approve Reservation *(v0.2)*

```mermaid
flowchart TD
    start([POST /reservations/id/decision]) --> op{caller is a<br/>box office operator?}
    op -- no --> e403[/403/]
    op -- yes --> r{reservation exists?}
    r -- no --> e404[/404/]
    r -- yes --> dec{decision is<br/>approve or reject?}
    dec -- no --> e422[/422/]
    dec -- yes --> tx[[BEGIN IMMEDIATE]]
    tx --> d{stored state = PENDING_APPROVAL?}
    d -- no --> e409a[/409 invalid state<br/>covers deciding twice and losing to Cancel/]
    d -- yes --> t{now < starts_at?}
    t -- no --> e409b[/409 approval expired/]
    t -- yes --> which{decision}
    which -- reject --> rej[UPDATE reservation AND all its seats to REJECTED]
    rej --> okr([200 REJECTED, seats free again])
    which -- approve --> upd[UPDATE reservation AND all its seats to CONFIRMED]
    upd --> uq{uq_confirmed_seat_per_screening<br/>violated?}
    uq -- yes --> rb[ROLLBACK - nothing confirmed]
    rb --> e409c[/409 seat taken/]
    uq -- no --> commit[COMMIT]
    commit --> notify[NotificationService.reservation_confirmed<br/>outcome ignored]
    notify --> oka([200 CONFIRMED])
```

## 4. Consistency check

Each edge of the state diagram, traced to the text and to an executed test
(`tests/test_c02_operations.py`). Rows for v0.2 have **no test yet**: the application has not been
changed for v0.2, so those examples are written but not run.

| Transition / rule | Spec | Test |
|---|---|---|
| `[*] → DRAFT` on create | OP-01 | `TestCreate::test_valid_request_makes_a_draft_with_a_15_minute_hold` |
| create refused after start | OP-01, BR-01 | `TestCreate::test_screening_that_already_started_is_409` |
| `DRAFT → CONFIRMED` | OP-03 | `TestConfirm::test_draft_becomes_confirmed_together_with_its_seats` |
| `DRAFT → CANCELLED` | OP-04, BR-03 | `TestCancel::test_draft_before_start_is_cancelled_and_row_is_kept` |
| `CONFIRMED → CANCELLED` | OP-04, BR-03 | `TestCancel::test_confirmed_before_start_is_cancelled_and_seats_are_free_again` |
| `DRAFT → EXPIRED` (derived) | OP-02 timing example, ADR-003 | `TestAvailability::test_hold_boundary_is_exclusive`, `TestConfirm::test_expired_hold_reads_as_expired_and_holds_no_seats` |
| no arrow out of `CANCELLED` | BR-03 | `TestCancel::test_already_cancelled_is_409`, `TestConfirm::test_cancelled_is_409` |
| no arrow out of `EXPIRED` | BR-03 | `TestCancel::test_expired_is_409`, `TestConfirm::test_expired_hold_is_409` |
| cancel refused after start (both states) | BR-03 | `TestCancel::test_confirmed_after_start_is_409`, `::test_draft_after_start_is_409` |
| one winner among concurrent creates | BR-02, OP-01 | `TestCreate::test_two_simultaneous_creates_on_one_seat_give_exactly_one_201` |
| one winner among concurrent confirms | BR-02, OP-03 | `TestConfirm::test_two_simultaneous_confirms_on_one_seat_give_exactly_one_200` |
| confirm is all-or-nothing | OP-03 | `TestConfirm::test_multi_seat_conflict_leaves_no_seat_confirmed` |
| `DRAFT → PENDING_APPROVAL` *(v0.2)* | OP-03 variant | *not yet written* |
| pending seats read `occupied` for hours *(v0.2)* | OP-02, OP-05 | *not yet written* |
| `PENDING_APPROVAL → CONFIRMED` *(v0.2)* | OP-05 | *not yet written* |
| `PENDING_APPROVAL → REJECTED`, seats freed *(v0.2)* | OP-05 | *not yet written* |
| `PENDING_APPROVAL → CANCELLED` *(v0.2)* | OP-04 | *not yet written* |
| `PENDING_APPROVAL → EXPIRED` at `starts_at` *(v0.2)* | OP-05 | *not yet written* |
| approve versus cancel race: one winner *(v0.2)* | OP-05 | *not yet written* |
| non-operator decision → `403` *(v0.2)* | OP-05 | *not yet written* |
