"""Donations: create a PayPal order, capture it, and keep the books straight.

The flow is two calls from the browser. ``create_order`` checks the case can take money and asks
PayPal for an order; after the donor approves it in PayPal's window, ``capture_order`` takes the
money. Capture holds a lock on the case and then on the donation (every money path locks in that
order, so two requests cannot deadlock), re-checks that the case still accepts money *before*
asking PayPal to capture, and then writes the donation, the ledger entries and the case totals
together.

``record_capture`` does that last part for any capture it is handed, so the webhook handler can
complete a donation whose browser closed before the capture call returned. Every step is safe to
repeat: PayPal calls carry an idempotency key derived from the donation, and a donation that is
already captured is returned as it is.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, Conflict, NotFound, ValidationFailed
from app.logging import scrub
from app.models.case import Case
from app.models.enums import (
    ActorType,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    LedgerEntryType,
)
from app.models.ledger import LedgerEntry
from app.models.money import MAX_DONATION_USD_MINOR, MIN_DONATION_USD_MINOR, Donation
from app.money import format_amount, format_money, to_minor, usd_minor_to_kes_minor
from app.payments import errors as paypal_errors
from app.payments import parsing
from app.payments.errors import PayPalError
from app.payments.paypal_client import get_client
from app.security import crypto
from app.services import (
    audit_service,
    case_service,
    fx_service,
    ledger_service,
    mail_service,
    settlement_service,
)

logger = logging.getLogger(__name__)

DONOR_NAME_MAX = 40
DONOR_EMAIL_CONTEXT = "donor_email"
# Statuses that mean the money was taken; a repeat capture returns the donation as it is.
TAKEN_STATUSES = frozenset(
    {
        DonationStatus.CAPTURED,
        DonationStatus.PARTIALLY_REFUNDED,
        DonationStatus.REFUNDED,
        DonationStatus.REVERSED,
    }
)


class CaseNotAccepting(Conflict):
    code = "case_not_accepting"
    message = "This case is not taking donations right now."


class CaseFullyFunded(Conflict):
    code = "case_fully_funded"
    message = "This case no longer needs more donations. You have not been charged."


class PaymentPending(Conflict):
    code = "payment_pending"
    message = "PayPal is still checking this payment. We will email your receipt when it clears."


class PaymentDeclined(AppError):
    status_code = 422
    code = "payment_declined"
    message = "PayPal did not complete this payment. You have not been charged."


@dataclass(frozen=True)
class CaptureResult:
    """What a capture call produced. ``fresh`` is false when the donation was already captured."""

    donation: Donation
    fresh: bool


def _order_request_id(donation_id: uuid.UUID) -> str:
    return f"order-{donation_id}"


def _capture_request_id(donation_id: uuid.UUID) -> str:
    return f"capture-{donation_id}"


def _field_error(field: str, message: str, code: str) -> ValidationFailed:
    return ValidationFailed(message, code=code, details={"json": {field: [message]}})


# --------------------------------------------------------------------------- validation


def parse_amount(value: Decimal) -> int:
    """US cents from the donor's amount, between $1 and $10,000 with at most two decimals."""
    try:
        minor = to_minor(value)
        exact = Decimal(minor) / 100 == value
    except (InvalidOperation, ValueError) as exc:
        raise _field_error("amount_usd", "Enter an amount in dollars.", "amount_invalid") from exc
    if not exact:
        raise _field_error("amount_usd", "Use at most two decimal places.", "amount_invalid")
    if not MIN_DONATION_USD_MINOR <= minor <= MAX_DONATION_USD_MINOR:
        low, high = format_amount(MIN_DONATION_USD_MINOR), format_amount(MAX_DONATION_USD_MINOR)
        raise _field_error("amount_usd", f"Give between ${low} and ${high}.", "amount_out_of_range")
    return minor


def clean_donor_name(name: str | None) -> str | None:
    """The name shown beside a donation on the public ledger: short, plain, no contact details."""
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return None
    if len(cleaned) > DONOR_NAME_MAX or not cleaned.isprintable():
        raise _field_error(
            "donor_display_name", f"Use at most {DONOR_NAME_MAX} plain characters.", "name_invalid"
        )
    if scrub(cleaned) != cleaned:
        raise _field_error(
            "donor_display_name",
            "Do not put an email address or phone number in the name.",
            "name_personal_data",
        )
    return cleaned


# --------------------------------------------------------------------------- creating an order


def create_order(
    session: Session,
    *,
    case_slug: str,
    amount_usd: Decimal,
    excess_choice: ExcessChoice,
    donor_display_name: str | None,
    is_anonymous: bool,
) -> tuple[Donation, dict[str, Any]]:
    """Ask PayPal for an order for a public case that is taking money. The caller commits.

    The donation row is written only after PayPal has answered, because the order ID is part of
    the row; if PayPal fails, nothing is stored. Returns the donation and PayPal's order.
    """
    minor = parse_amount(amount_usd)
    name = None if is_anonymous else clean_donor_name(donor_display_name)
    case = case_service.get_public_case_or_404(session, case_slug)
    if not case_service.is_accepting_donations(case):
        raise CaseNotAccepting()

    donation_id = uuid.uuid4()
    order = get_client().create_order(
        amount_usd_minor=minor,
        reference_id=str(case.id),
        custom_id=str(donation_id),
        description=f"TibaDirect donation for case {case.public_slug}",
        request_id=_order_request_id(donation_id),
    )
    order_id = order.get("id")
    if not isinstance(order_id, str):
        raise paypal_errors.malformed("order has no id")

    donation = Donation(
        id=donation_id,
        case=case,
        paypal_order_id=order_id,
        amount_usd_minor=minor,
        excess_choice=excess_choice,
        donor_display_name=name,
        is_anonymous=is_anonymous,
    )
    session.add(donation)
    session.flush()
    audit_service.record(
        "donation.created",
        donation,
        actor=ActorType.SYSTEM,
        details={"case_id": case.id, "amount_usd_minor": minor},
    )
    return donation, order


# --------------------------------------------------------------------------- capturing


def _lock_donation_of_order(session: Session, order_id: str) -> tuple[Case, Donation]:
    """Find the donation for an order, then lock its case and the donation, in that order."""
    case_id = session.execute(
        select(Donation.case_id).where(Donation.paypal_order_id == order_id)
    ).scalar_one_or_none()
    if case_id is None:
        raise NotFound("Donation not found.", code="donation_not_found")
    case = case_service.lock_case(session, case_id)
    donation = session.execute(
        select(Donation)
        .where(Donation.paypal_order_id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return case, donation


def capture_order(session: Session, order_id: str) -> CaptureResult:
    """Take the money for an order the donor has approved. The caller commits on success.

    * Already captured: returns the donation, no call to PayPal.
    * The case stopped accepting money since the order was made: nothing is captured, the donation
      becomes ``not_captured_case_closed`` and the donor is told they were not charged (409).
    * Otherwise PayPal is asked to capture, once, under the donation's own idempotency key.
    """
    case, donation = _lock_donation_of_order(session, order_id)
    if donation.status in TAKEN_STATUSES:
        return CaptureResult(donation, fresh=False)
    if donation.status == DonationStatus.NOT_CAPTURED_CASE_CLOSED:
        raise CaseFullyFunded()
    if donation.status == DonationStatus.DENIED:
        raise PaymentDeclined()

    if not case_service.is_accepting_donations(case):
        donation.status = DonationStatus.NOT_CAPTURED_CASE_CLOSED
        audit_service.record(
            "donation.not_captured",
            donation,
            actor=ActorType.SYSTEM,
            details={"case_id": case.id, "case_status": case.status},
        )
        session.commit()
        raise CaseFullyFunded()

    client = get_client()
    try:
        order = client.capture_order(order_id, request_id=_capture_request_id(donation.id))
    except PayPalError as error:
        if error.code != "order_already_captured":
            raise
        # An earlier attempt was captured but never recorded (we crashed after PayPal answered).
        order = client.get_order(order_id)

    details = parsing.from_order(order)
    if details.status == "PENDING":
        donation.paypal_capture_id = details.capture_id
        session.commit()
        raise PaymentPending()
    if not details.completed:
        donation.status = DonationStatus.DENIED
        audit_service.record(
            "donation.denied",
            donation,
            actor=ActorType.SYSTEM,
            details={"case_id": case.id, "paypal_status": details.status},
        )
        session.commit()
        raise PaymentDeclined()

    record_capture(session, donation, case, details, actor=ActorType.SYSTEM)
    return CaptureResult(donation, fresh=True)


def record_capture(
    session: Session,
    donation: Donation,
    case: Case,
    details: parsing.CaptureDetails,
    *,
    actor: ActorType,
    request_id: str | None = None,
) -> bool:
    """Write a completed capture: the donation, ledger entries, case totals and the funded move.

    ``case`` and ``donation`` must be locked by the caller, the case first. Returns False, changing
    nothing, if the donation was already captured. Money PayPal has already taken is always
    recorded, even if the case has stopped accepting more; the excess policy sorts that out when
    the case ends.
    """
    if donation.status in TAKEN_STATUSES:
        return False

    amount_matches = details.gross_usd_minor == donation.amount_usd_minor
    donation.amount_usd_minor = details.gross_usd_minor
    donation.paypal_capture_id = details.capture_id
    donation.paypal_fee_usd_minor = details.fee_usd_minor
    donation.net_usd_minor = details.net_usd_minor
    rate = fx_service.latest(session)
    donation.fx_rate_id = rate.id
    donation.fx_rate = rate
    # What the case receives, not what the donor paid: the PayPal fee is not the hospital's.
    donation.kes_equivalent_minor = usd_minor_to_kes_minor(details.net_usd_minor, rate.rate)
    donation.status = DonationStatus.CAPTURED
    donation.captured_at = datetime.now(UTC)
    if details.payer_email:
        donation.donor_email_enc = details.payer_email.encode()
        donation.donor_email_hmac = crypto.lookup_hash(
            details.payer_email, context=DONOR_EMAIL_CONTEXT
        )
    session.flush()

    entries = [
        ledger_service.NewEntry(
            case.id,
            LedgerEntryType.DONATION_CAPTURED,
            LedgerAccount.CASE_FUNDS,
            details.gross_usd_minor,
            "USD",
            "donation",
            donation.id,
        )
    ]
    if details.fee_usd_minor:
        entries += [
            ledger_service.NewEntry(
                case.id,
                LedgerEntryType.PAYPAL_FEE,
                account,
                sign * details.fee_usd_minor,
                "USD",
                "donation",
                donation.id,
            )
            for account, sign in ((LedgerAccount.CASE_FUNDS, -1), (LedgerAccount.PAYPAL_FEES, 1))
        ]
    ledger_service.append(session, entries)

    session.expire(case, ["donations"])
    case_service.recompute_totals(case)
    audit_service.record(
        "donation.captured",
        donation,
        actor=actor,
        request_id=request_id,
        details={
            "case_id": case.id,
            "amount_usd_minor": details.gross_usd_minor,
            "fee_usd_minor": details.fee_usd_minor,
            "amount_matched_order": amount_matches,
        },
    )
    case_service.sync_funding(case)
    session.commit()
    settlement_service.enqueue_check(case.id)
    send_receipt(donation, details.payer_email)
    return True


# --------------------------------------------------------------------------- receipts


def _app_url(path: str) -> str:
    return f"{current_app.config['APP_BASE_URL'].rstrip('/')}{path}"


def send_receipt(donation: Donation, email: str | None) -> bool:
    """Email the donor their receipt. A failure is logged and never undoes the donation."""
    if not email:
        return False
    case = donation.case
    try:
        mail_service.send(
            "donation_receipt",
            email,
            language=mail_service.DEFAULT_LANGUAGE,
            alias=case.alias,
            amount=format_money(donation.amount_usd_minor, "USD"),
            fee=format_money(donation.paypal_fee_usd_minor, "USD"),
            net=format_money(donation.net_usd_minor, "USD"),
            kes_estimate=format_money(donation.kes_equivalent_minor, "KES"),
            ledger_url=_app_url(f"/c/{case.public_slug}#ledger"),
            receipt_url=_app_url(f"/receipt/{donation.receipt_token}"),
        )
    except mail_service.MailError as exc:
        logger.error("receipt email failed", extra={"reason": str(exc)})
        return False
    return True


def get_receipt_or_404(session: Session, token: str) -> Donation:
    """The donation behind a receipt link. Only a donation whose money was taken has a receipt."""
    donation = session.execute(
        select(Donation).where(Donation.receipt_token == token)
    ).scalar_one_or_none()
    if donation is None or donation.status not in TAKEN_STATUSES:
        raise NotFound("Receipt not found.", code="receipt_not_found")
    return donation


def receipt_view(session: Session, donation: Donation) -> dict[str, Any]:
    """The donor-facing receipt. It holds no email address, no IDs and no full hash."""
    entry = session.execute(
        select(LedgerEntry).where(
            LedgerEntry.ref_type == "donation",
            LedgerEntry.ref_id == donation.id,
            LedgerEntry.entry_type == LedgerEntryType.DONATION_CAPTURED,
        )
    ).scalar_one_or_none()
    case = donation.case
    return {
        "receipt_token": donation.receipt_token,
        "status": donation.status.value,
        "amount_usd_minor": donation.amount_usd_minor,
        "paypal_fee_usd_minor": donation.paypal_fee_usd_minor,
        "net_usd_minor": donation.net_usd_minor,
        "refunded_usd_minor": donation.refunded_usd_minor,
        "kes_estimate_minor": donation.kes_equivalent_minor,
        "fx_rate": f"{donation.fx_rate.rate:.8f}" if donation.fx_rate else None,
        "excess_choice": donation.excess_choice.value,
        "donor_display_name": None if donation.is_anonymous else donation.donor_display_name,
        "captured_at": donation.captured_at,
        "case_slug": case.public_slug,
        "case_alias": case.alias,
        "hospital_name": case.hospital.display_name,
        "ledger_reference": ledger_service.short_reference(donation.paypal_capture_id),
        "ledger_hash_short": entry.hash[: ledger_service.SHORT_HASH_LENGTH] if entry else None,
    }
