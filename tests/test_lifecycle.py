"""C05 unit tests: the lifecycle policy (src/cinema/lifecycle.py) alone - plain values, no database, no clock.

Derived from the Detailed Design in docs/architecture-and-decisions.md ("C05 - Detailed Design"); the ids
U1-U5 are those of the table in docs/evidence-and-evolution.md ("C05 DD Verification Evidence"); U6 was
added with spec v0.3 ("Change after C05: Confirm is start-guarded (v0.3)" in the same file).
"""

from __future__ import annotations

import itertools

import pytest

from cinema.lifecycle import Command, Refused, ReservationFacts, Transition, effective_state, plan

AT = "2030-01-01T17:00:00Z"
EARLIER = "2030-01-01T16:00:00Z"
LATER = "2030-01-01T18:00:00Z"

STORED_STATES = ["DRAFT", "PENDING_APPROVAL", "CONFIRMED", "CANCELLED", "REJECTED"]


def facts(state="DRAFT", held=True, started=False, needs_approval=False) -> ReservationFacts:
    return ReservationFacts(
        stored_state=state,
        hold_until=LATER if held else EARLIER,
        starts_at=EARLIER if started else LATER,
        needs_approval=needs_approval,
    )


def diagram_edge(command, state, held, started, needs_approval) -> Transition | None:
    """The C02 state diagram (docs/diagrams.md section 2), written out independently of TRANSITIONS."""
    draft_alive = state == "DRAFT" and held
    pending_alive = state == "PENDING_APPROVAL" and not started
    if command == Command.CONFIRM and draft_alive and not started:
        return Transition("PENDING_APPROVAL", None) if needs_approval else Transition("CONFIRMED", "confirmed_at")
    if command == Command.APPROVE and pending_alive:
        return Transition("CONFIRMED", "confirmed_at")
    if command == Command.REJECT and pending_alive:
        return Transition("REJECTED", None)
    if command == Command.CANCEL and not started and (draft_alive or pending_alive or state == "CONFIRMED"):
        return Transition("CANCELLED", "cancelled_at")
    return None


# U1 - interface contract of plan; TRANSITIONS equals the state diagram
def test_plan_allows_exactly_the_edges_of_the_state_diagram():
    combinations = list(itertools.product(Command, STORED_STATES, [True, False], [True, False], [True, False]))
    assert len(combinations) == 160
    for command, state, held, started, needs_approval in combinations:
        result = plan(command, facts(state, held, started, needs_approval), AT)  # must not raise
        expected = diagram_edge(command, state, held, started, needs_approval)
        case = (command, state, held, started, needs_approval)
        if expected is None:
            assert isinstance(result, Refused), case
        else:
            assert result == expected, case
            assert result.target != "EXPIRED", case


# U2 - algorithm branch R4 (decision-table rows 1 and 2)
def test_confirm_chooses_pending_approval_only_when_a_seat_needs_it():
    assert plan(Command.CONFIRM, facts(needs_approval=True), AT) == Transition("PENDING_APPROVAL", None)
    assert plan(Command.CONFIRM, facts(needs_approval=False), AT) == Transition("CONFIRMED", "confirmed_at")


# U3 - R1 effective state, half-open boundaries of BR-01
def test_effective_state_boundaries_are_half_open():
    one_second_later = "2030-01-01T17:00:01Z"
    assert effective_state(ReservationFacts("DRAFT", AT, LATER, False), AT) == "EXPIRED"
    assert effective_state(ReservationFacts("DRAFT", one_second_later, LATER, False), AT) == "DRAFT"
    assert effective_state(ReservationFacts("PENDING_APPROVAL", EARLIER, AT, False), AT) == "EXPIRED"
    assert effective_state(ReservationFacts("PENDING_APPROVAL", EARLIER, one_second_later, False), AT) == "PENDING_APPROVAL"
    for state in ["CONFIRMED", "CANCELLED", "REJECTED"]:  # no hold, no expiry
        assert effective_state(facts(state, held=False, started=True), AT) == state


# U4 - R3 is separate from R1 (decision-table row 12)
def test_cancel_of_a_live_draft_after_the_start_is_refused_by_the_start_guard():
    live_draft_started = facts("DRAFT", held=True, started=True)
    assert effective_state(live_draft_started, AT) == "DRAFT"
    assert plan(Command.CANCEL, live_draft_started, AT) == Refused("The screening has already started")


# U5 - failure behaviour: the reason names the derived state (decision-table rows 5 and 9)
@pytest.mark.parametrize("command", [Command.CONFIRM, Command.APPROVE, Command.REJECT])
def test_refusal_names_the_derived_state(command):
    expired_pending = facts("PENDING_APPROVAL", started=True)
    assert plan(command, expired_pending, AT) == Refused(f"Cannot {command} a EXPIRED reservation")


# U6 - R3 also guards confirm (decision-table row 16, spec v0.3)
def test_confirm_of_a_live_draft_after_the_start_is_refused_by_the_start_guard():
    live_draft_started = facts("DRAFT", held=True, started=True, needs_approval=True)
    assert effective_state(live_draft_started, AT) == "DRAFT"
    assert plan(Command.CONFIRM, live_draft_started, AT) == Refused("The screening has already started")
