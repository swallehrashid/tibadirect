"""Refunds: creating one, asking PayPal to make it, and applying it to the books.

A refund is written down first (``create_refund``, status ``pending``, money set aside so nothing
else can use it) and made afterwards by a job (``execute_refund``) under its own idempotency key,
so a repeat can never refund twice. ``complete_refund`` is the part every path shares: the
donation, the ledger and the case totals change together. The webhook handler uses it to finish a
refund PayPal reports, or to record one made outside the system.

Callers lock the case first and then the donation, as every money path does.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import Conflict
from app.jobs import queue as jobs
from app.logging import scrub
from app.models.case import Case
from app.models.enums import (
    ActorType,
    CaseStatus,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    RefundStatus,
    ReviewSource,
)
from app.models.money import Donation, Refund
from app.money import format_money
from app.payments import errors as paypal_errors
from app.payments.errors import PayPalError
from app.payments.paypal_client import get_client
from app.services import (
    audit_service,
    case_service,
    ledger_service,
    mail_service,
    review_service,
)

logger = logging.getLogger(__name__)

REFUND_NOTE = "Refund of a TibaDirect donation that the case did not need"
# Provider problems that need a person, not a retry: the refund stays pending until fixed.
CONFIGURATION_ERRORS = frozenset({"paypal_auth_failed", "paypal_bad_response"})
REFUND_REASON_TEXT = {
    RefundReason.EXCESS_POLICY: "the case did not need all the money given",
    RefundReason.FREEZE: "the case was paused for review",
    RefundReason.CANCELLATION: "the case ended before the bill was paid",
    RefundReason.ADMIN: "our team asked for it",
}


def find_by_paypal_id(session: Session, paypal_refund_id: str) -> Refund | None:
    return session.execute(
        select(Refund).where(Refund.paypal_refund_id == paypal_refund_id)
    ).scalar_one_or_none()


def pending_for(session: Session, donation: Donation) -> list[Refund]:
    """Refunds we asked for that PayPal has not confirmed yet, oldest first."""
    return list(
        session.execute(
            select(Refund)
            .where(Refund.donation_id == donation.id, Refund.status == RefundStatus.PENDING)
            .order_by(Refund.created_at)
        ).scalars()
    )


def refundable_usd_minor(donation: Donation) -> int:
    """What the case still holds of this donation: net minus paid out, refunded and pooled."""
    return (
        donation.net_usd_minor
        - donation.allocated_usd_minor
        - donation.refunded_usd_minor
        - donation.pooled_usd_minor
    )


def pending_usd_minor(session: Session, donation: Donation) -> int:
    """Refunds asked for but not yet made: set aside, so not available for anything else."""
    return int(
        session.execute(
            select(func.coalesce(func.sum(Refund.amount_usd_minor), 0)).where(
                Refund.donation_id == donation.id, Refund.status == RefundStatus.PENDING
            )
        ).scalar_one()
    )


def pending_by_donation(session: Session, case_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Pending refund totals for each donation of a case that has any."""
    rows = session.execute(
        select(Refund.donation_id, func.sum(Refund.amount_usd_minor))
        .join(Donation, Refund.donation_id == Donation.id)
        .where(Donation.case_id == case_id, Refund.status == RefundStatus.PENDING)
        .group_by(Refund.donation_id)
    ).all()
    return {donation_id: int(total) for donation_id, total in rows}


def stale_pending_ids(session: Session, now: datetime) -> list[uuid.UUID]:
    """Refunds written down more than five minutes ago that PayPal has not been asked about, or
    has not finished (those PayPal answered "pending" are left to the refund webhook)."""
    cutoff = now - timedelta(minutes=5)
    return list(
        session.execute(
            select(Refund.id).where(
                Refund.status == RefundStatus.PENDING,
                Refund.paypal_refund_id.is_(None),
                Refund.created_at < cutoff,
            )
        ).scalars()
    )


def available_usd_minor(session: Session, donation: Donation) -> int:
    """What can still be refunded or pooled: held by the case and not already promised."""
    return max(0, refundable_usd_minor(donation) - pending_usd_minor(session, donation))


def complete_refund(
    session: Session,
    refund: Refund,
    case: Case,
    *,
    paypal_refund_id: str,
    actor: ActorType,
    request_id: str | None = None,
) -> bool:
    """Mark a refund succeeded and apply it. Returns False, changing nothing, if already done.

    The donation's refunded total grows, its status follows (partially refunded, then refunded
    when nothing is left), the ledger records the money leaving the case for the donor, and the
    case totals are recomputed. The caller commits.
    """
    if refund.status == RefundStatus.SUCCEEDED:
        return False
    donation = refund.donation
    if refund.amount_usd_minor > refundable_usd_minor(donation):
        raise ValueError("The refund is larger than what the donation still holds")

    refund.status = RefundStatus.SUCCEEDED
    refund.paypal_refund_id = paypal_refund_id
    refund.completed_at = datetime.now(UTC)
    refund.failure_reason = None
    donation.refunded_usd_minor += refund.amount_usd_minor
    donation.status = (
        DonationStatus.REFUNDED
        if donation.refunded_usd_minor >= donation.net_usd_minor
        else DonationStatus.PARTIALLY_REFUNDED
    )
    session.flush()

    ledger_service.append(
        session,
        [
            ledger_service.NewEntry(
                case.id,
                LedgerEntryType.REFUND_ISSUED,
                account,
                sign * refund.amount_usd_minor,
                "USD",
                "refund",
                refund.id,
            )
            for account, sign in ((LedgerAccount.CASE_FUNDS, -1), (LedgerAccount.DONOR_REFUNDS, 1))
        ],
    )
    session.expire(case, ["donations"])
    case_service.recompute_totals(case)
    case_service.sync_funding(case)
    audit_service.record(
        "refund.succeeded",
        refund,
        actor=actor,
        request_id=request_id,
        details={
            "case_id": case.id,
            "donation_id": donation.id,
            "amount_usd_minor": refund.amount_usd_minor,
            "reason": refund.reason,
        },
    )
    return True


def record_external_refund(
    session: Session,
    donation: Donation,
    case: Case,
    *,
    paypal_refund_id: str,
    amount_usd_minor: int,
    request_id: str | None = None,
) -> Refund | None:
    """A refund made in PayPal itself, not by us (for example from the dashboard).

    It is recorded so the ledger matches PayPal. A refund of the whole payment is larger than the
    net amount the case holds (PayPal keeps its fee), and one that cuts into money already paid to
    the hospital cannot come out of the case; the part that fits is applied and an admin is asked
    to look at the rest.
    """
    if find_by_paypal_id(session, paypal_refund_id) is not None:
        return None
    applied = max(0, min(amount_usd_minor, refundable_usd_minor(donation)))
    refund: Refund | None = None
    if applied:
        refund = Refund(
            id=uuid.uuid4(),
            donation=donation,
            amount_usd_minor=applied,
            reason=RefundReason.ADMIN,
            status=RefundStatus.PENDING,
            idempotency_key=f"external-{paypal_refund_id}"[:64],
            requested_by=ActorType.PAYPAL,
        )
        session.add(refund)
        session.flush()
        complete_refund(
            session,
            refund,
            case,
            paypal_refund_id=paypal_refund_id,
            actor=ActorType.PAYPAL,
            request_id=request_id,
        )
    if applied != amount_usd_minor:
        review_service.create_item(
            session,
            case,
            ReviewSource.ADMIN,
            "A refund made in PayPal is larger than what the case could return",
            payload=_unmatched_payload(donation, amount_usd_minor, applied),
        )
    return refund


def _unmatched_payload(donation: Donation, paypal_minor: int, applied: int) -> dict[str, Any]:
    return {
        "donation_id": str(donation.id),
        "paypal_refund_usd_minor": paypal_minor,
        "applied_usd_minor": applied,
    }


# --------------------------------------------------------------------------- creating and making


class RefundTooLarge(Conflict):
    code = "refund_exceeds_balance"
    message = "That is more than the case still holds of this donation."


def create_refund(
    session: Session,
    donation: Donation,
    case: Case,
    amount_usd_minor: int,
    reason: RefundReason,
    *,
    requested_by: ActorType = ActorType.SYSTEM,
    request_id: str | None = None,
) -> Refund:
    """Write down a refund and set the money aside. The caller commits, then queues
    ``enqueue_execute``. Caller holds the case and donation locks."""
    if amount_usd_minor <= 0:
        raise ValueError("A refund must be for more than nothing")
    if amount_usd_minor > available_usd_minor(session, donation):
        raise RefundTooLarge(
            details={
                "available_usd_minor": available_usd_minor(session, donation),
                "requested_usd_minor": amount_usd_minor,
            }
        )
    refund_id = uuid.uuid4()
    refund = Refund(
        id=refund_id,
        donation=donation,
        amount_usd_minor=amount_usd_minor,
        reason=reason,
        status=RefundStatus.PENDING,
        idempotency_key=f"tiba-refund-{refund_id.hex}",
        requested_by=requested_by,
    )
    session.add(refund)
    session.flush()
    audit_service.record(
        "refund.created",
        refund,
        actor=requested_by,
        request_id=request_id,
        details={
            "case_id": case.id,
            "donation_id": donation.id,
            "amount_usd_minor": amount_usd_minor,
            "reason": reason,
        },
    )
    return refund


def enqueue_execute(refund_id: uuid.UUID) -> None:
    """Queue the job that asks PayPal to make a refund. A queue outage is logged; the hourly
    sweep queues it again."""
    try:
        jobs.enqueue(jobs.QUEUE_PAYMENTS, "app.jobs.tasks.execute_refund", str(refund_id))
    except RedisError:
        logger.exception("could not queue a refund; the sweep will retry")


def _lock_refund(session: Session, refund_id: uuid.UUID) -> tuple[Case, Refund] | None:
    """The case, the donation and the refund, locked in that order, or None if unknown."""
    found = session.execute(
        select(Refund.id, Donation.case_id, Donation.id.label("donation_id"))
        .join(Donation, Refund.donation_id == Donation.id)
        .where(Refund.id == refund_id)
    ).first()
    if found is None:
        return None
    case = case_service.lock_case(session, found.case_id)
    session.execute(
        select(Donation)
        .where(Donation.id == found.donation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    refund = session.execute(
        select(Refund)
        .where(Refund.id == refund_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return case, refund


def execute_refund(
    session: Session, refund_id: uuid.UUID, *, request_id: str | None = None
) -> RefundStatus | None:
    """Ask PayPal to make a pending refund. Safe to repeat: PayPal sees the refund's own key.

    A provider outage rolls back and raises, so the queue retries. A refund PayPal answers
    "pending" keeps its PayPal ID and is finished by the refund webhook. One PayPal refuses is
    closed as failed and an admin is asked to look; the money stays with the case.
    """
    locked = _lock_refund(session, refund_id)
    if locked is None:
        return None
    case, refund = locked
    if refund.status != RefundStatus.PENDING:
        session.rollback()
        return refund.status
    donation = refund.donation
    if not donation.paypal_capture_id:
        _fail(session, case, refund, "The donation has no PayPal payment to refund", request_id)
        session.commit()
        return RefundStatus.FAILED
    try:
        answer = get_client().refund_capture(
            donation.paypal_capture_id,
            amount_usd_minor=refund.amount_usd_minor,
            note=REFUND_NOTE,
            request_id=refund.idempotency_key,
        )
    except PayPalError as error:
        if error.retryable or error.code in CONFIGURATION_ERRORS:
            session.rollback()
            raise
        _fail(session, case, refund, error.code, request_id)
        session.commit()
        return RefundStatus.FAILED

    paypal_id = answer.get("id") if isinstance(answer.get("id"), str) else None
    state = str(answer.get("status", "")).upper()
    if paypal_id is None:
        session.rollback()
        raise paypal_errors.malformed("refund has no id")
    if state == "COMPLETED":
        complete_refund(
            session,
            refund,
            case,
            paypal_refund_id=paypal_id,
            actor=ActorType.SYSTEM,
            request_id=request_id,
        )
        session.commit()
        send_refund_email(refund)
        _finish_freeze_if_frozen(case)
        return RefundStatus.SUCCEEDED
    if state == "PENDING":
        refund.paypal_refund_id = paypal_id  # the refund webhook finishes it
        session.commit()
        return RefundStatus.PENDING
    _fail(session, case, refund, state.lower() or "failed", request_id)
    session.commit()
    _finish_freeze_if_frozen(case)
    return RefundStatus.FAILED


def _finish_freeze_if_frozen(case: Case) -> None:
    """A refund just ended on a paused case: have the freeze finished if that was the last one."""
    if case.status != CaseStatus.FROZEN:
        return
    try:
        jobs.enqueue(jobs.QUEUE_PAYMENTS, "app.jobs.tasks.finish_frozen_case", str(case.id))
    except RedisError:
        logger.exception("could not queue the end of a freeze; the sweep will retry")


def _fail(
    session: Session, case: Case, refund: Refund, reason: str, request_id: str | None
) -> None:
    refund.status = RefundStatus.FAILED
    refund.failure_reason = scrub(reason)[:300]
    refund.completed_at = datetime.now(UTC)
    session.flush()
    audit_service.record(
        "refund.failed",
        refund,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={"case_id": case.id, "donation_id": refund.donation_id, "reason": refund.reason},
    )
    review_service.create_item(
        session,
        case,
        ReviewSource.ADMIN,
        "A refund to a donor could not be made",
        payload={
            "refund_id": str(refund.id),
            "donation_id": str(refund.donation_id),
            "amount_usd_minor": refund.amount_usd_minor,
            "reason": refund.failure_reason,
        },
    )


def send_refund_email(refund: Refund) -> bool:
    """Tell the donor their refund was made, if PayPal gave us their address. Never raises."""
    donation = refund.donation
    if not donation.donor_email_enc:
        return False
    try:
        mail_service.send(
            "refund_issued",
            donation.donor_email_enc.decode(),
            language=mail_service.DEFAULT_LANGUAGE,
            alias=donation.case.alias,
            amount=format_money(refund.amount_usd_minor, "USD"),
            reason=REFUND_REASON_TEXT.get(refund.reason, REFUND_REASON_TEXT[RefundReason.ADMIN]),
        )
    except mail_service.MailError as exc:
        logger.error("refund email failed", extra={"reason": str(exc)})
        return False
    return True
