"""What the treasurer agent may propose, and what an administrator's approval does (PAY-11).

The agent only ever writes ``agent_actions`` rows in the status ``proposed``. Nothing moves until a
signed-in administrator approves one, and approving runs the ordinary service for it (a refund, a
tranche check, a freeze), so every rule those services enforce still applies. A proposal that has
become impossible by the time it is approved is recorded as ``failed`` with the reason.

Every step is audited: the proposal with actor ``agent``; the decision and the execution with
actor ``agent`` and the administrator as approver.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.errors import AppError, Conflict, NotFound, ValidationFailed
from app.models.case import Case
from app.models.enums import (
    ActorType,
    AgentActionStatus,
    AgentActionType,
    CaseStatus,
    DonationStatus,
    RefundReason,
)
from app.models.identity import User
from app.models.money import Donation
from app.models.platform import AgentAction
from app.services import (
    admin_service,
    audit_service,
    case_ending_service,
    case_service,
    refund_service,
    settlement_service,
)

logger = logging.getLogger(__name__)

MIN_REASON = 10
MAX_REASON = 500
REFUNDABLE_STATUSES = (DonationStatus.CAPTURED, DonationStatus.PARTIALLY_REFUNDED)
FREEZABLE_STATUSES = (
    CaseStatus.PENDING_CONFIRMATION,
    CaseStatus.LIVE,
    CaseStatus.FUNDED,
)
MAX_ERROR_CHARS = 500


class ProposalRejected(ValidationFailed):
    """A proposal that cannot be made; the message goes back to the agent so it can say why."""

    code = "proposal_rejected"


def _reason(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) < MIN_REASON:
        raise ProposalRejected(f"Give a reason of at least {MIN_REASON} characters.")
    return cleaned[:MAX_REASON]


def _existing(
    session: Session, action_type: AgentActionType, case_id: uuid.UUID, payload: dict[str, Any]
) -> AgentAction | None:
    """The same proposal, still waiting: the agent asking twice must not queue it twice."""
    waiting = session.scalars(
        select(AgentAction).where(
            AgentAction.action_type == action_type,
            AgentAction.case_id == case_id,
            AgentAction.status == AgentActionStatus.PROPOSED,
        )
    )
    return next((a for a in waiting if a.payload == payload), None)


def _create(
    session: Session,
    admin: User,
    action_type: AgentActionType,
    case: Case,
    payload: dict[str, Any],
    rationale: str,
) -> AgentAction:
    duplicate = _existing(session, action_type, case.id, payload)
    if duplicate is not None:
        return duplicate
    action = AgentAction(
        action_type=action_type, case_id=case.id, payload=payload, rationale=rationale
    )
    session.add(action)
    session.flush()
    audit_service.record(
        "agent_action.proposed",
        action,
        actor=ActorType.AGENT,
        approver=admin,
        details={"case_id": case.id, "action_type": action_type, "payload": payload},
    )
    return action


# --------------------------------------------------------------------------- proposing


def propose_refund(
    session: Session, admin: User, *, donation_id: uuid.UUID, amount_usd_minor: int, reason: str
) -> AgentAction:
    donation = session.get(Donation, donation_id)
    if donation is None:
        raise ProposalRejected("There is no such donation.")
    if donation.status not in REFUNDABLE_STATUSES:
        raise ProposalRejected(f"A {donation.status.value} donation cannot be refunded.")
    if amount_usd_minor <= 0:
        raise ProposalRejected("A refund must be for more than nothing.")
    available = refund_service.available_usd_minor(session, donation)
    if amount_usd_minor > available:
        raise ProposalRejected(
            f"Only {available} US cents of this donation is still held; "
            f"{amount_usd_minor} was asked for."
        )
    return _create(
        session,
        admin,
        AgentActionType.REFUND,
        donation.case,
        {"donation_id": str(donation.id), "amount_usd_minor": amount_usd_minor},
        _reason(reason),
    )


def propose_payout(
    session: Session, admin: User, *, case_id: uuid.UUID, reason: str
) -> AgentAction:
    case = session.get(Case, case_id)
    if case is None:
        raise ProposalRejected("There is no such case.")
    if case.status not in settlement_service.SETTLEABLE_STATUSES:
        raise ProposalRejected(f"A {case.status.value} case does not take payouts.")
    return _create(session, admin, AgentActionType.PAYOUT, case, {}, _reason(reason))


def propose_freeze(
    session: Session, admin: User, *, case_id: uuid.UUID, reason: str
) -> AgentAction:
    case = session.get(Case, case_id)
    if case is None:
        raise ProposalRejected("There is no such case.")
    if case.status not in FREEZABLE_STATUSES:
        raise ProposalRejected(f"A {case.status.value} case cannot be frozen.")
    return _create(session, admin, AgentActionType.FREEZE, case, {}, _reason(reason))


# --------------------------------------------------------------------------- listing


def actions(status: AgentActionStatus | None = None) -> Select[tuple[AgentAction]]:
    """Proposals, the ones waiting first, then the newest."""
    statement = select(AgentAction)
    if status is not None:
        statement = statement.where(AgentAction.status == status)
    waiting_first = (AgentAction.status != AgentActionStatus.PROPOSED).asc()
    return statement.order_by(waiting_first, AgentAction.created_at.desc(), AgentAction.id)


def get_or_404(session: Session, action_id: uuid.UUID, *, lock: bool = False) -> AgentAction:
    action = session.get(AgentAction, action_id, with_for_update=lock)
    if action is None:
        raise NotFound("No such action.", code="action_not_found")
    return action


# --------------------------------------------------------------------------- deciding


def reject(
    session: Session, action_id: uuid.UUID, admin: User, note: str, *, request_id: str | None = None
) -> AgentAction:
    """Turn a proposal down, with a written reason. The caller commits."""
    cleaned = _note(note)
    admin_id = admin.id  # read first: loading it later could flush a half-made decision
    action = get_or_404(session, action_id, lock=True)
    _require_waiting(action)
    action.status = AgentActionStatus.REJECTED
    action.decided_by_id = admin_id
    action.decided_at = datetime.now(UTC)
    action.result = {"note": cleaned}
    audit_service.record(
        "agent_action.rejected",
        action,
        actor=ActorType.AGENT,
        approver=admin,
        request_id=request_id,
        details={"case_id": action.case_id, "action_type": action.action_type},
    )
    session.flush()
    return action


def approve(
    session: Session, action_id: uuid.UUID, admin: User, *, request_id: str | None = None
) -> AgentAction:
    """Approve a proposal and carry it out through the normal service. Commits.

    If the action can no longer be done (the case has moved on, the donation has been refunded),
    it is recorded as ``failed`` with the reason and returned; nothing it started is kept.
    """
    admin_id = admin.id
    action = get_or_404(session, action_id, lock=True)
    _require_waiting(action)
    action.status = AgentActionStatus.APPROVED
    action.decided_by_id = admin_id
    action.decided_at = datetime.now(UTC)
    audit_service.record(
        "agent_action.approved",
        action,
        actor=ActorType.AGENT,
        approver=admin,
        request_id=request_id,
        details={"case_id": action.case_id, "action_type": action.action_type},
    )
    session.commit()

    try:
        result, after_commit = _EXECUTORS[action.action_type](session, action, admin, request_id)
        action = session.get(AgentAction, action_id, with_for_update=True)  # type: ignore[assignment]
        action.status = AgentActionStatus.EXECUTED
        action.executed_at = datetime.now(UTC)
        action.result = result
        audit_service.record(
            "agent_action.executed",
            action,
            actor=ActorType.AGENT,
            approver=admin,
            request_id=request_id,
            details={"case_id": action.case_id, "action_type": action.action_type},
        )
        session.commit()
    except AppError as error:
        session.rollback()
        return _failed(session, action_id, admin, error, request_id)
    except Exception:
        # A bug must not leave the proposal stuck as "approved": record it, then let it surface.
        session.rollback()
        _failed(
            session,
            action_id,
            admin,
            AppError("The action could not be carried out.", code="internal_error"),
            request_id,
        )
        raise
    after_commit()
    return session.get(AgentAction, action_id)  # type: ignore[return-value]


def _failed(
    session: Session,
    action_id: uuid.UUID,
    admin: User,
    error: AppError,
    request_id: str | None,
) -> AgentAction:
    action = get_or_404(session, action_id, lock=True)
    action.status = AgentActionStatus.FAILED
    action.error = f"{error.code}: {error.message}"[:MAX_ERROR_CHARS]
    audit_service.record(
        "agent_action.failed",
        action,
        actor=ActorType.AGENT,
        approver=admin,
        request_id=request_id,
        details={
            "case_id": action.case_id,
            "action_type": action.action_type,
            "error_code": error.code,
        },
    )
    session.commit()
    return action


def _note(note: str) -> str:
    cleaned = " ".join((note or "").split())
    if not MIN_REASON <= len(cleaned) <= MAX_REASON:
        raise ValidationFailed(
            f"Say why, in {MIN_REASON} to {MAX_REASON} characters.", code="note_required"
        )
    return cleaned


def _require_waiting(action: AgentAction) -> None:
    if action.status != AgentActionStatus.PROPOSED:
        raise Conflict(
            f"This action was already {action.status.value}.", code="action_already_decided"
        )


# --------------------------------------------------------------------------- executing

Executed = tuple[dict[str, Any], Callable[[], None]]


def _do_refund(
    session: Session, action: AgentAction, admin: User, request_id: str | None
) -> Executed:
    case = case_service.lock_case(session, action.case_id)  # type: ignore[arg-type]
    donation = session.execute(
        select(Donation)
        .where(Donation.id == uuid.UUID(action.payload["donation_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if donation.status not in REFUNDABLE_STATUSES:
        raise Conflict(
            f"The donation is now {donation.status.value}.", code="donation_not_refundable"
        )
    refund = refund_service.create_refund(
        session,
        donation,
        case,
        int(action.payload["amount_usd_minor"]),
        RefundReason.ADMIN,
        requested_by=ActorType.AGENT,
        request_id=request_id,
    )
    refund_id = refund.id
    return (
        {"refund_id": str(refund_id), "amount_usd_minor": refund.amount_usd_minor},
        lambda: refund_service.enqueue_execute(refund_id),
    )


def _do_payout(
    session: Session, action: AgentAction, admin: User, request_id: str | None
) -> Executed:
    # Creating a tranche commits and queues the sending itself, so only the outcome is kept here.
    result = settlement_service.run_settlement_check(
        session,
        action.case_id,
        request_id=request_id,  # type: ignore[arg-type]
    )
    if result.settlement is None:
        raise Conflict(f"No tranche is due: {result.reason}.", code="no_tranche_due")
    settlement = result.settlement
    return (
        {
            "settlement_id": str(settlement.id),
            "tranche_no": settlement.tranche_no,
            "amount_usd_minor": settlement.amount_usd_minor,
        },
        lambda: None,
    )


def _do_freeze(
    session: Session, action: AgentAction, admin: User, request_id: str | None
) -> Executed:
    case = session.get(Case, action.case_id)
    assert case is not None
    ended = admin_service.freeze(session, case, admin, action.rationale)
    return (
        {"refunds": len(ended.refund_ids)},
        lambda: case_ending_service.after_commit(ended, frozen=True, case=case),
    )


_EXECUTORS: dict[AgentActionType, Callable[..., Executed]] = {
    AgentActionType.REFUND: _do_refund,
    AgentActionType.PAYOUT: _do_payout,
    AgentActionType.FREEZE: _do_freeze,
}
