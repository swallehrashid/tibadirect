"""Refunds: applying a refund PayPal has made to the donation, the ledger and the case totals.

This holds the part every refund path shares. Creating a refund and asking PayPal to make it
arrive with the excess policy (P3-F07); the webhook handler (P3-F04) uses what is here to finish
a refund PayPal reports, or to record one made outside the system.

Callers lock the donation and then the case before calling, as the donation service does.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.enums import (
    ActorType,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    RefundStatus,
    ReviewSource,
)
from app.models.money import Donation, Refund
from app.services import audit_service, case_service, ledger_service, review_service


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
    """What can still go back to the donor: the net amount not yet paid out or refunded."""
    return donation.net_usd_minor - donation.allocated_usd_minor - donation.refunded_usd_minor


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
