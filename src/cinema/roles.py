"""Who a user is to the cinema: the actors of the Project Frame, stored in `users.role`.

A new login is a VIEWER. Staff accounts are seeded (`seed.sql`). There are no passwords yet, so a role
is only as trustworthy as the e-mail login itself (`POST /auth/login`)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "VIEWER"  # the customer: reserves, confirms and cancels seats
    BOX_OFFICE = "BOX_OFFICE"  # the operator who sells tickets: approves / rejects (OP-05), cancels on request
    ADMIN = "ADMIN"  # the cinema manager: the programme (POST /movies), and anything the box office does


# Who may decide a PENDING_APPROVAL reservation (OP-05).
OPERATORS = frozenset({Role.BOX_OFFICE, Role.ADMIN})
