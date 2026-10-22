"""Ending a case early: the family cancels it, or an admin freezes it.

*Cancelling* (the family, for example because the treatment was cancelled or the patient died)
stops donations at once, cancels tranches not yet sent, and starts the excess policy, so each
donor's money follows the choice they made.

*Freezing* (an admin who suspects fraud) stops donations and hides the page at once, cancels
tranches not yet sent (payouts already at the provider are allowed to finish), and refunds
every donor in full whatever they chose. When the last refund is made and nothing is on its
way, the case moves from ``frozen`` to ``cancelled`` with the reason ``fraud``.

All of it runs under the case lock, in one transaction, and the refunds are made afterwards by
the same jobs as any other refund.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Conflict
from app.jobs import queue as jobs
from app.models.case import Case
from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    DonationStatus,
    RefundReason,
    SettlementStatus,
)
from app.models.identity import User
from app.models.money import Donation
from app.services import (
    audit_service,
    case_service,
    excess_policy_service,
    notification_service,
    refund_service,
    settlement_service,
)

logger = logging.getLogger(__name__)

# Family reason codes and the close reason each is recorded as. There is no separate "other"
# close reason, so a family's own reason is kept in the audit trail and the case closes as the
# treatment having been cancelled.
FAMILY_REASONS: dict[str, CloseReason] = {
    "treatment_cancelled": CloseReason.TREATMENT_CANCELLED,
    "patient_deceased": CloseReason.PATIENT_DECEASED,
    "other": CloseReason.TREATMENT_CANCELLED,
}
CANCELLABLE_STATUSES = frozenset(
    {
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
    }
)
HOLDING_STATUSES = (DonationStatus.CAPTURED, DonationStatus.PARTIALLY_REFUNDED)
FREEZE_REASON_TEXT = "the case was paused for review"


class CaseNotCancellable(Conflict):
    code = "case_not_cancellable"
    message = "This case cannot be cancelled from where it is."


@dataclass
class Ended:
    """What an ending needs done after the transaction commits."""

    refund_ids: list[uuid.UUID] = field(default_factory=list)
    case_id: uuid.UUID | None = None


# --------------------------------------------------------------------------- cancelling


def cancel_case(session: Session, case: Case, user: User, reason: str) -> Ended:
    """The family cancels their case. The caller commits, then calls ``after_commit``.

    Allowed from the moment the case is submitted until it is closed. Donations stop at once,
    unsent tranches are cancelled and the excess policy then places each donor's money.
    """
    if reason not in FAMILY_REASONS:
        raise Conflict("Unknown reason.", code="reason_unknown")
    case = case_service.lock_case(session, case.id)
    if case.status not in CANCELLABLE_STATUSES:
        raise CaseNotCancellable(
            details={"status": case.status.value},
        )
    case_service.transition(
        case, CaseStatus.CANCELLED, user, reason, close_reason=FAMILY_REASONS[reason]
    )
    unsent = settlement_service.cancel_unsent(session, case, "the case was cancelled")
    audit_service.record(
        "case.cancelled_by_family",
        case,
        actor=user,
        details={"family_reason": reason, "unsent_tranches_cancelled": unsent},
    )
    return Ended(case_id=case.id)


# --------------------------------------------------------------------------- freezing


def _signal_codes(case: Case) -> list[str]:
    result = case_service.latest_triage(case)
    codes: list[str] = []
    for signal in (result.signals if result else None) or []:
        code = signal.get("code") if isinstance(signal, dict) else signal
        if isinstance(code, str):
            codes.append(code)
    return codes


def freeze_case(session: Session, case: Case, admin: User, reason: str) -> Ended:
    """Pause a case the admin suspects and start paying the donors back. The caller commits,
    then calls ``after_commit``.

    The page is hidden and donations stop at once and the trust level drops (that is the
    transition itself). Tranches not yet sent are cancelled, every donor's leftover gets a
    refund of reason ``freeze`` regardless of their choice, and the triage signals of the case
    are put in the audit trail so the team can learn from it.
    """
    cleaned = " ".join(reason.split())
    case = case_service.lock_case(session, case.id)
    if case.status == CaseStatus.FROZEN:
        raise Conflict("This case is already frozen.", code="case_already_frozen")
    case_service.transition(case, CaseStatus.FROZEN, admin, cleaned)
    settlement_service.cancel_unsent(session, case, FREEZE_REASON_TEXT)
    donations = _locked_holding_donations(session, case)
    refund_ids = excess_policy_service.refund_all(session, case, donations, RefundReason.FREEZE)
    triage = case_service.latest_triage(case)
    audit_service.record(
        "case.freeze_signals",
        case,
        actor=admin,
        details={
            "signals": _signal_codes(case),
            "risk_score": triage.risk_score if triage else None,
            "refunds": len(refund_ids),
        },
    )
    return Ended(refund_ids=refund_ids, case_id=case.id)


def _locked_holding_donations(session: Session, case: Case) -> list[Donation]:
    return list(
        session.execute(
            select(Donation)
            .where(Donation.case_id == case.id, Donation.status.in_(HOLDING_STATUSES))
            .order_by(Donation.captured_at, Donation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )


def after_commit(ended: Ended, *, frozen: bool = False, case: Case | None = None) -> None:
    """Queue the work an ending started, once it is saved: the refunds, then (for a cancelled
    case) the excess policy, or (for a frozen one) the check that finishes the freeze."""
    for refund_id in ended.refund_ids:
        refund_service.enqueue_execute(refund_id)
    if ended.case_id is None:
        return
    if frozen:
        enqueue_finish(ended.case_id)
        if case is not None:
            notification_service.notify_family(case, "case_frozen")
    else:
        excess_policy_service.enqueue_run(ended.case_id)


# --------------------------------------------------------------------------- finishing a freeze


def finish_frozen(session: Session, case_id: uuid.UUID, *, request_id: str | None = None) -> str:
    """Move a frozen case on to ``cancelled`` (reason fraud) once every donor has been paid back.

    Returns what it found: ``cancelled``, ``waiting`` (a refund or a payout is still on its
    way), ``blocked`` (money is held and no refund is on its way, for example one failed; an
    admin has been told) or ``not_frozen``. Money that appears while frozen (a payout that
    failed and released its money, or a late payment PayPal reports) is refunded too.
    """
    case = case_service.lock_case(session, case_id)
    if case.status != CaseStatus.FROZEN:
        session.rollback()
        return "not_frozen"
    donations = _locked_holding_donations(session, case)
    new_refunds = excess_policy_service.refund_all(
        session, case, donations, RefundReason.FREEZE, request_id=request_id
    )
    pending_refunds = any(refund_service.pending_usd_minor(session, d) for d in donations)
    in_flight = any(
        s.status in (SettlementStatus.PENDING, SettlementStatus.PROCESSING)
        for s in case.settlements
    )
    if new_refunds or pending_refunds or in_flight:
        session.commit()
        for refund_id in new_refunds:
            refund_service.enqueue_execute(refund_id)
        return "waiting"
    if any(refund_service.available_usd_minor(session, d) > 0 for d in donations):
        session.rollback()
        return "blocked"
    case_service.transition(
        case,
        CaseStatus.CANCELLED,
        ActorType.SYSTEM,
        "frozen_case_repaid",
        close_reason=CloseReason.FRAUD,
        request_id=request_id,
    )
    session.commit()
    return "cancelled"


def enqueue_finish(case_id: uuid.UUID) -> None:
    """Queue the check that finishes a freeze. A queue outage is logged; the hourly sweep
    catches it."""
    try:
        jobs.enqueue(jobs.QUEUE_PAYMENTS, "app.jobs.tasks.finish_frozen_case", str(case_id))
    except RedisError:
        logger.exception("could not queue the end of a freeze; the sweep will retry")


def sweep_frozen(session: Session, now: datetime | None = None) -> int:
    """Queue the finishing check for every frozen case. Returns how many."""
    _ = now or datetime.now(UTC)
    ids = list(session.execute(select(Case.id).where(Case.status == CaseStatus.FROZEN)).scalars())
    for case_id in ids:
        enqueue_finish(case_id)
    return len(ids)


def describe(case: Case) -> dict[str, Any]:
    """What the family may be told about an ended case, for the family view."""
    return {
        "status": case.status.value,
        "close_reason": case.close_reason.value if case.close_reason else None,
    }
