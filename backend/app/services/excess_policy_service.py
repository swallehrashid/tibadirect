"""What happens to money a case did not need.

When a case ends (closed, or cancelled) the money still held for each donation, its *leftover*,
follows the donor's own choice made at checkout:

* ``refund``: sent back through PayPal, unless it is smaller than ``REFUND_MIN_USD_MINOR``
  (a refund of a few cents costs more than it returns), in which case it goes to the pool.
* ``solidarity_pool``: moved to the solidarity pool on the ledger.
* ``follow_up_care``: held for ``FOLLOW_UP_HOLD_DAYS`` in case the same patient needs more care;
  when the hold expires it is refunded like any other.

Money is never sent anywhere a donor did not choose. The policy is safe to run again at any time:
a refund asked for, or money pooled, is no longer leftover, so a repeat finds nothing to do.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants
from app.jobs import queue as jobs
from app.models.case import Case
from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
)
from app.models.money import Donation
from app.services import audit_service, case_service, ledger_service, refund_service

logger = logging.getLogger(__name__)

ENDED_STATUSES = frozenset({CaseStatus.CLOSED, CaseStatus.CANCELLED})
HOLDING_STATUSES = (DonationStatus.CAPTURED, DonationStatus.PARTIALLY_REFUNDED)


@dataclass
class PolicyResult:
    """What a run did. ``reason`` is ``ran`` or why it did nothing."""

    reason: str = "ran"
    refunds: list[uuid.UUID] = field(default_factory=list)
    pooled_usd_minor: int = 0
    held_usd_minor: int = 0


def leftover_usd_minor(session: Session, donation: Donation) -> int:
    """Money held for a donation that nothing has claimed: not paid out, refunded, pooled or
    already being refunded."""
    return refund_service.available_usd_minor(session, donation)


def _pool(
    session: Session, case: Case, donation: Donation, amount: int, request_id: str | None
) -> None:
    donation.pooled_usd_minor += amount
    session.flush()
    ledger_service.append(
        session,
        [
            ledger_service.NewEntry(
                case.id,
                LedgerEntryType.POOL_TRANSFER,
                account,
                sign * amount,
                "USD",
                "donation",
                donation.id,
            )
            for account, sign in (
                (LedgerAccount.CASE_FUNDS, -1),
                (LedgerAccount.SOLIDARITY_POOL, 1),
            )
        ],
    )
    audit_service.record(
        "donation.pooled",
        donation,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={"case_id": case.id, "amount_usd_minor": amount, "choice": donation.excess_choice},
    )


def refund_all(
    session: Session,
    case: Case,
    donations: list[Donation],
    reason: RefundReason,
    *,
    request_id: str | None = None,
) -> list[uuid.UUID]:
    """Write down a refund for everything still held for each donation, whatever the donor chose
    and however small (a case paused for review gives every donor their money back). The case and
    donations are locked by the caller, who commits and then queues the refunds."""
    refund_ids = []
    for donation in donations:
        left = leftover_usd_minor(session, donation)
        if left <= 0:
            continue
        refund = refund_service.create_refund(
            session, donation, case, left, reason, request_id=request_id
        )
        refund_ids.append(refund.id)
    return refund_ids


def run_excess_policy(
    session: Session,
    case_id: uuid.UUID,
    *,
    now: datetime | None = None,
    request_id: str | None = None,
) -> PolicyResult:
    """Apply each donor's choice to what the case no longer needs. Commits when it acts.

    Only a closed or cancelled case has leftovers to settle; any other status does nothing.
    """
    now = now or datetime.now(UTC)
    policy = constants.policy()
    case = case_service.lock_case(session, case_id)
    if case.status not in ENDED_STATUSES:
        session.rollback()
        return PolicyResult("case_not_ended")

    donations = list(
        session.execute(
            select(Donation)
            .where(Donation.case_id == case.id, Donation.status.in_(HOLDING_STATUSES))
            .order_by(Donation.captured_at, Donation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    result = PolicyResult()
    if case.close_reason == CloseReason.FRAUD:
        # A case ended for suspected fraud returns every donor's money, whatever they chose.
        result.refunds = refund_all(
            session, case, donations, RefundReason.FREEZE, request_id=request_id
        )
        donations = []
    for donation in donations:
        left = leftover_usd_minor(session, donation)
        if left <= 0:
            continue
        choice = donation.excess_choice
        if choice == ExcessChoice.FOLLOW_UP_CARE:
            if case.follow_up_until is None:
                case.follow_up_until = now + timedelta(days=policy.follow_up_hold_days)
                audit_service.record(
                    "case.follow_up_hold_started",
                    case,
                    actor=ActorType.SYSTEM,
                    request_id=request_id,
                    details={"days": policy.follow_up_hold_days},
                )
            if now < case.follow_up_until:
                result.held_usd_minor += left
                continue
            choice = ExcessChoice.REFUND  # the hold is over: the same rule as any refund
        if choice == ExcessChoice.SOLIDARITY_POOL or left < policy.refund_min_usd_minor:
            _pool(session, case, donation, left, request_id)
            result.pooled_usd_minor += left
            continue
        reason = (
            RefundReason.CANCELLATION
            if case.status == CaseStatus.CANCELLED
            else RefundReason.EXCESS_POLICY
        )
        refund = refund_service.create_refund(
            session, donation, case, left, reason, request_id=request_id
        )
        result.refunds.append(refund.id)

    audit_service.record(
        "excess_policy.ran",
        case,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={
            "refunds": len(result.refunds),
            "pooled_usd_minor": result.pooled_usd_minor,
            "held_usd_minor": result.held_usd_minor,
        },
    )
    session.commit()
    for refund_id in result.refunds:
        refund_service.enqueue_execute(refund_id)
    return result


# --------------------------------------------------------------------------- queueing and sweeps


def enqueue_run(case_id: uuid.UUID) -> None:
    """Queue the policy for a case that has just ended. Call after the commit that ended it. A
    queue outage is logged; the hourly sweep makes up for it."""
    try:
        jobs.enqueue(jobs.QUEUE_PAYMENTS, "app.jobs.tasks.run_excess_policy", str(case_id))
    except RedisError:
        logger.exception("could not queue the excess policy; the sweep will retry")


def sweep(session: Session, now: datetime | None = None) -> int:
    """The hourly safety net. Queues the policy for every ended case that still has leftover
    money to place, including follow-up holds that have run out, and re-queues refunds left
    pending. Returns how many jobs it queued."""
    now = now or datetime.now(UTC)
    queued = 0
    ended = session.execute(
        select(Case).where(Case.status.in_(ENDED_STATUSES)).order_by(Case.updated_at)
    ).scalars()
    for case in ended:
        due = False
        for donation in case.donations:
            if donation.status not in HOLDING_STATUSES:
                continue
            if leftover_usd_minor(session, donation) <= 0:
                continue
            on_hold = (
                donation.excess_choice == ExcessChoice.FOLLOW_UP_CARE
                and case.follow_up_until is not None
                and now < case.follow_up_until
            )
            if not on_hold:
                due = True
                break
        if due:
            enqueue_run(case.id)
            queued += 1
    for refund_id in refund_service.stale_pending_ids(session, now):
        refund_service.enqueue_execute(refund_id)
        queued += 1
    return queued
