"""Lifecycle policy (C05 Detailed Design): which transition of an existing Reservation is allowed, and into what.

Pure: no database, no clock (the instant `at` is passed in), no exceptions for domain outcomes - a refusal is
returned as `Refused`. Reservation Management (`reservations.py`) loads the facts inside its write transaction,
asks `plan`, and performs the result; it is still the only module that writes Reservation state (ADR-008).

Times are ISO-8601 UTC strings (`seed.iso`), compared as text."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Command(StrEnum):
    CONFIRM = "confirm"
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ReservationFacts:
    """What the policy needs to know about one reservation, as stored."""

    stored_state: str
    hold_until: str
    starts_at: str
    needs_approval: bool


@dataclass(frozen=True)
class Transition:
    target: str
    timestamp_column: str | None  # "confirmed_at", "cancelled_at" or None


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class Rule:
    allowed_from: frozenset[str]  # effective states
    needs_not_started: bool  # BR-01: only while at < starts_at
    target: Transition | None  # None: chosen in `plan` (confirm)


# The state machine of docs/diagrams.md section 2, as data. CANCELLED, REJECTED and EXPIRED are in no
# `allowed_from`: that is what "terminal" (BR-03) means. Confirm is not start-guarded (OP-03; see DD 3.2).
TRANSITIONS: dict[Command, Rule] = {
    Command.CONFIRM: Rule(frozenset({"DRAFT"}), False, None),
    Command.APPROVE: Rule(frozenset({"PENDING_APPROVAL"}), True, Transition("CONFIRMED", "confirmed_at")),
    Command.REJECT: Rule(frozenset({"PENDING_APPROVAL"}), True, Transition("REJECTED", None)),
    Command.CANCEL: Rule(
        frozenset({"DRAFT", "PENDING_APPROVAL", "CONFIRMED"}), True, Transition("CANCELLED", "cancelled_at")
    ),
}


def effective_state(facts: ReservationFacts, at: str) -> str:
    """R1: the state as everyone sees it. Expiry is derived, never stored (ADR-003)."""
    if facts.stored_state == "DRAFT" and facts.hold_until <= at:
        return "EXPIRED"
    if facts.stored_state == "PENDING_APPROVAL" and facts.starts_at <= at:
        return "EXPIRED"
    return facts.stored_state


def plan(command: Command, facts: ReservationFacts, at: str) -> Transition | Refused:
    """R2-R4: allowed from the effective state? screening not started? which target?"""
    rule = TRANSITIONS[command]
    state = effective_state(facts, at)
    if state not in rule.allowed_from:
        return Refused(f"Cannot {command} a {state} reservation")
    if rule.needs_not_started and facts.starts_at <= at:
        return Refused("The screening has already started")
    if command == Command.CONFIRM:  # v0.2: a seat that requires approval waits for a box office decision
        return Transition("PENDING_APPROVAL", None) if facts.needs_approval else Transition("CONFIRMED", "confirmed_at")
    return rule.target
