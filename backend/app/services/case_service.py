"""Case lifecycle rules.

``transition`` is the only place a case changes status. It checks the move against the state
machine, keeps ``trust_level`` and the timestamps in step, and writes the audit entry. Creating
and editing drafts, totals and the public views are added by the case endpoints (P2-F07).
"""

from datetime import UTC, datetime
from typing import Any

from app.errors import Conflict
from app.models.case import Case
from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    ConsentScope,
    TrustLevel,
)
from app.models.identity import User
from app.services import audit_service

S = CaseStatus

# The lifecycle diagram from the design: each status and where it may go next.
ALLOWED_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    S.DRAFT: frozenset({S.PENDING_TRIAGE, S.CANCELLED}),
    S.PENDING_TRIAGE: frozenset({S.PENDING_CONFIRMATION, S.IN_REVIEW, S.FROZEN, S.CANCELLED}),
    S.IN_REVIEW: frozenset({S.PENDING_CONFIRMATION, S.CANCELLED, S.FROZEN}),
    S.PENDING_CONFIRMATION: frozenset({S.LIVE, S.IN_REVIEW, S.CANCELLED, S.FROZEN}),
    S.LIVE: frozenset({S.FUNDED, S.CLOSED, S.CANCELLED, S.FROZEN}),
    S.FUNDED: frozenset({S.LIVE, S.CLOSED, S.CANCELLED, S.FROZEN}),
    S.FROZEN: frozenset({S.CANCELLED}),
    S.CLOSED: frozenset(),
    S.CANCELLED: frozenset(),
}

# Statuses in which a family may still change the case or give consent.
OPEN_STATUSES = frozenset(
    {S.DRAFT, S.PENDING_TRIAGE, S.IN_REVIEW, S.PENDING_CONFIRMATION, S.LIVE, S.FUNDED}
)
# Statuses before the hospital has confirmed anything.
UNCONFIRMED_STATUSES = frozenset({S.DRAFT, S.PENDING_TRIAGE, S.IN_REVIEW, S.PENDING_CONFIRMATION})


class InvalidTransition(Conflict):
    """The case cannot move from its current status to the requested one."""

    code = "invalid_transition"
    message = "The case cannot move to that status from where it is."


def can_transition(current: CaseStatus, target: CaseStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def _trust_level(case: Case, target: CaseStatus) -> TrustLevel:
    """Trust follows status: confirmed from ``live`` on, and never again after a freeze."""
    if target == S.PENDING_CONFIRMATION:
        return TrustLevel.PENDING_CONFIRMATION
    if target in {S.LIVE, S.FUNDED, S.CLOSED}:
        return TrustLevel.HOSPITAL_CONFIRMED
    if target == S.CANCELLED:
        return case.trust_level
    return TrustLevel.UNVERIFIED  # draft, pending_triage, in_review, frozen


def transition(
    case: Case,
    to: CaseStatus,
    actor: User | ActorType,
    reason: str | None = None,
    *,
    close_reason: CloseReason | None = None,
    approver: User | None = None,
    now: datetime | None = None,
) -> Case:
    """Move ``case`` to ``to`` and audit it. The caller commits.

    ``reason`` is a short code or sentence for the audit trail; for a freeze it is also stored on
    the case and is required. Closing needs a ``close_reason``. Going live needs an active
    ``public_page`` consent. Anything the diagram does not allow raises ``InvalidTransition``.
    """
    current = case.status
    if not can_transition(current, to):
        raise InvalidTransition(
            f"A case that is {current.value} cannot become {to.value}.",
            details={"from": current.value, "to": to.value},
        )
    if to == S.CLOSED and close_reason is None:
        raise InvalidTransition(
            "Closing a case needs a close reason.", code="close_reason_required"
        )
    if to == S.FROZEN and not (reason and reason.strip()):
        raise InvalidTransition("Freezing a case needs a reason.", code="freeze_reason_required")
    if (
        current == S.PENDING_CONFIRMATION
        and to == S.LIVE
        and case.active_consent(ConsentScope.PUBLIC_PAGE) is None
    ):
        raise InvalidTransition(
            "The family has not agreed to a public page, so the case cannot go live.",
            code="public_consent_required",
        )

    stamp = now or datetime.now(UTC)
    case.status = to
    case.trust_level = _trust_level(case, to)
    if current == S.DRAFT and to == S.PENDING_TRIAGE:
        case.submitted_at = stamp
    if to == S.LIVE and case.live_at is None:
        case.live_at = stamp
    if to == S.FUNDED:
        case.funded_at = stamp
    if current == S.FUNDED and to == S.LIVE:
        case.funded_at = None
    if to == S.CLOSED:
        case.closed_at = stamp
        case.close_reason = close_reason
    if to == S.FROZEN:
        case.frozen_at = stamp
        case.freeze_reason = (reason or "").strip()[:300]

    details: dict[str, Any] = {"from": current, "to": to}
    if reason and to != S.FROZEN:
        details["reason"] = reason
    audit_service.record("case.transitioned", case, actor=actor, approver=approver, details=details)
    return case
