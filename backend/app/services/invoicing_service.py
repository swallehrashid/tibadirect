"""Invoicing as the hospital (specification 8.10, PAY-07).

When a hospital has PayPal credentials, each confirmed bill becomes a PayPal invoice issued by the
hospital to the collector account, and each tranche that reaches the hospital is recorded on it as a
payment. The hospital sees the invoice and its status in its portal, and ends up with a paper trail
in its own PayPal account that matches what it was paid.

* One invoice per confirmation: "Medical bill <invoice number>", one item, in US dollars at the rate
  of the day, no clinical text. A later confirmation (a final bill) replaces an invoice nobody has
  paid anything on, which is cancelled; one that has had payments is left to the hospital.
* A tranche is recorded as its share of the invoice (the tranche's part of the confirmed amount, in
  dollars), so the shares add up to the invoice exactly even if the rate moved between the two.
* Invoicing never stands in the way of money. Every step is a job that can be repeated: the invoice
  ID is saved before it is sent, the payment ID before the status is read back, and a sweep makes up
  for anything that was missed. A refusal from PayPal opens a review item instead of retrying.
"""

import logging
import uuid
from datetime import UTC, date, datetime

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs import queue as jobs
from app.logging import scrub
from app.models.enums import (
    ActorType,
    ConfirmationStatus,
    ReviewSource,
    SettlementStatus,
)
from app.models.hospital import FacilityRegisterEntry, Hospital
from app.models.money import Settlement
from app.models.verification import Confirmation
from app.money import format_money, kes_minor_to_usd_minor
from app.payments.errors import PayPalError
from app.payments.paypal_client import (
    InvoicingGateway,
    get_invoicing_client,
    invoice_id_from,
)
from app.services import audit_service, fx_service, review_service

logger = logging.getLogger(__name__)

# PayPal's invoice states in which nothing more will happen on its own.
FINISHED = frozenset({"PAID", "MARKED_AS_PAID", "CANCELLED", "REFUNDED", "MARKED_AS_REFUNDED"})
# States in which an invoice can still be cancelled because nobody has paid anything on it.
CANCELLABLE = frozenset({"DRAFT", "SENT", "SCHEDULED", "UNPAID"})
# Kept on the confirmation / settlement when we gave up and a person has to look.
FAILED = "FAILED"
SKIPPED = "SKIPPED"
SWEEP_REFRESH_LIMIT = 25
NUMBER_PREFIX = "TD-"
MAX_ITEM_NAME = 200


def collector_email() -> str:
    from flask import current_app

    return str(current_app.config.get("PAYPAL_COLLECTOR_EMAIL") or "")


def client_for(hospital: Hospital) -> InvoicingGateway | None:
    """The hospital's invoicing client, or None if invoicing is not set up for it."""
    if not collector_email():
        return None
    return get_invoicing_client(hospital.register_entry.register_code)


def configured(hospital: Hospital) -> bool:
    return client_for(hospital) is not None


def _today() -> str:
    return date.today().isoformat()


def invoice_number_for(confirmation: Confirmation) -> str:
    """PayPal's own invoice number: short, unique per confirmation, free of any bill detail."""
    return NUMBER_PREFIX + confirmation.id.hex[:12].upper()


def invoice_usd_for(session: Session, confirmation: Confirmation) -> int:
    """The bill in US cents at the latest rate (never less than one cent)."""
    assert confirmation.amount_kes_minor is not None
    rate = fx_service.latest(session).rate
    return max(1, kes_minor_to_usd_minor(confirmation.amount_kes_minor, rate))


def payment_share(confirmation: Confirmation, settlement: Settlement) -> int:
    """This tranche's part of the invoice in US cents.

    The invoice total times the share of the confirmed amount paid so far, less what the earlier
    tranches came to, so the parts sum to the invoice whatever the rate did. Failed and cancelled
    tranches do not count; the ones in flight do, so the answer does not change as they land.
    """
    total_kes = confirmation.amount_kes_minor or 0
    total_usd = confirmation.invoice_usd_minor or 0
    if total_kes <= 0:
        return 0

    def cumulative(kes: int) -> int:
        return total_usd if kes >= total_kes else total_usd * kes // total_kes

    live = [
        other
        for other in confirmation.settlements
        if other.status != SettlementStatus.FAILED and other.tranche_no < settlement.tranche_no
    ]
    before = sum(other.amount_kes_minor for other in live)
    return cumulative(before + settlement.amount_kes_minor) - cumulative(before)


# --------------------------------------------------------------------------- issuing


def issue(session: Session, confirmation_id: uuid.UUID, *, request_id: str | None = None) -> str:
    """Create and send the invoice for a confirmed bill. Safe to repeat. Returns what happened.

    An outage rolls back and raises so the queue retries; a refusal marks the invoice failed and
    opens a review item.
    """
    confirmation = session.get(Confirmation, confirmation_id, with_for_update=True)
    if confirmation is None:
        return "missing"
    if confirmation.status != ConfirmationStatus.CONFIRMED or not confirmation.amount_kes_minor:
        session.rollback()
        return "not_confirmed"
    client = client_for(confirmation.hospital)
    if client is None:
        session.rollback()
        return "not_configured"
    if confirmation.invoice_status == FAILED or confirmation.invoice_status in FINISHED:
        session.rollback()
        return "already_done"

    try:
        if confirmation.paypal_invoice_id is None:
            usd = invoice_usd_for(session, confirmation)
            result = client.create_invoice(
                invoice_number=invoice_number_for(confirmation),
                recipient_email=collector_email(),
                item_name=f"Medical bill {confirmation.invoice_number or ''}".strip()[
                    :MAX_ITEM_NAME
                ],
                amount_usd_minor=usd,
                note=f"{format_money(confirmation.amount_kes_minor, 'KES')} bill confirmed by "
                "billing desk",
                invoice_date=_today(),
                request_id=f"invoice-{confirmation.id}",
            )
            invoice_id = invoice_id_from(result)
            if invoice_id is None:
                raise PayPalError(
                    status_code=502,
                    code="paypal_rejected",
                    message="PayPal gave no invoice ID.",
                    issue="NO_INVOICE_ID",
                )
            confirmation.paypal_invoice_id = invoice_id
            confirmation.invoice_status = "DRAFT"
            confirmation.invoice_usd_minor = usd
            # Keep the ID before the next call can fail, so a retry sends this invoice and does
            # not draft another one.
            session.commit()
            confirmation = session.get(Confirmation, confirmation_id, with_for_update=True)
            assert confirmation is not None
        if confirmation.invoice_status == "DRAFT":
            client.send_invoice(
                confirmation.paypal_invoice_id or "", request_id=f"invoice-send-{confirmation.id}"
            )
            confirmation.invoice_status = "SENT"
            audit_service.record(
                "invoice.issued",
                confirmation,
                actor=ActorType.SYSTEM,
                request_id=request_id,
                details={
                    "case_id": confirmation.case_id,
                    "invoice_usd_minor": confirmation.invoice_usd_minor,
                },
            )
            session.commit()
    except PayPalError as error:
        return _refused(session, confirmation_id, error, request_id)

    supersede(session, confirmation, request_id=request_id)
    return "issued"


def _refused(
    session: Session, confirmation_id: uuid.UUID, error: PayPalError, request_id: str | None
) -> str:
    """An outage is the queue's to retry; a refusal is a person's to look at."""
    session.rollback()
    if error.retryable or error.code in ("paypal_auth_failed",):
        raise error
    confirmation = session.get(Confirmation, confirmation_id, with_for_update=True)
    if confirmation is None:
        return "missing"
    confirmation.invoice_status = FAILED
    review_service.create_item(
        session,
        confirmation.case,
        ReviewSource.ADMIN,
        "The hospital's PayPal invoice could not be issued",
        payload={
            "origin": "invoice_failed",
            "confirmation_id": str(confirmation.id),
            "paypal_issue": scrub(error.issue or error.code)[:100],
        },
    )
    audit_service.record(
        "invoice.failed",
        confirmation,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={"case_id": confirmation.case_id, "issue": error.issue or error.code},
    )
    session.commit()
    return "failed"


def supersede(session: Session, newer: Confirmation, *, request_id: str | None = None) -> int:
    """Cancel the invoices of this case's older confirmations, once ``newer`` has been invoiced,
    where nobody has paid anything on them.

    Only a confirmation finalised strictly before ``newer`` is replaced, so two confirmations
    with the same timestamp never cancel each other. Returns how many were cancelled. A
    cancellation PayPal refuses is logged and left; the hospital can cancel it in its own account.
    """
    key = newer.finalized_at or newer.created_at
    older_ones = session.scalars(
        select(Confirmation).where(
            Confirmation.case_id == newer.case_id,
            Confirmation.id != newer.id,
            Confirmation.status == ConfirmationStatus.CONFIRMED,
            Confirmation.finalized_at < key,
        )
    ).all()
    cancelled = 0
    for older in older_ones:
        if older.paypal_invoice_id is None or older.invoice_status not in CANCELLABLE:
            continue
        client = client_for(older.hospital)
        if client is None:
            continue
        try:
            client.cancel_invoice(
                older.paypal_invoice_id,
                note="Replaced by a newer confirmed bill",
                request_id=f"invoice-cancel-{older.id}",
            )
        except PayPalError as error:
            logger.warning("could not cancel a replaced invoice", extra={"issue": error.issue})
            continue
        older.invoice_status = "CANCELLED"
        audit_service.record(
            "invoice.cancelled",
            older,
            actor=ActorType.SYSTEM,
            request_id=request_id,
            details={"case_id": older.case_id, "reason": "replaced"},
        )
        cancelled += 1
    if cancelled:
        session.commit()
    return cancelled


# --------------------------------------------------------------------------- tranche payments


def record_payment(
    session: Session, settlement_id: uuid.UUID, *, request_id: str | None = None
) -> str:
    """Record a paid tranche on its invoice. Safe to repeat: PayPal sees the same request ID and
    the payment ID is kept. Returns what happened."""
    settlement = session.get(Settlement, settlement_id, with_for_update=True)
    if settlement is None:
        return "missing"
    if settlement.status != SettlementStatus.SUCCEEDED:
        session.rollback()
        return "not_paid"
    if settlement.invoice_payment_id is not None:
        session.rollback()
        return "already_recorded"
    confirmation = settlement.confirmation
    client = client_for(confirmation.hospital)
    if client is None or confirmation.paypal_invoice_id is None:
        session.rollback()
        return "no_invoice"
    if confirmation.invoice_status in (None, "DRAFT"):
        session.rollback()
        return "invoice_not_sent"
    if confirmation.invoice_status in ("CANCELLED", FAILED):
        settlement.invoice_payment_id = SKIPPED
        session.commit()
        return "invoice_closed"

    amount = payment_share(confirmation, settlement)
    if amount <= 0:
        settlement.invoice_payment_id = SKIPPED
        session.commit()
        return "nothing_to_record"
    try:
        result = client.record_invoice_payment(
            confirmation.paypal_invoice_id,
            amount_usd_minor=amount,
            payment_date=(settlement.completed_at or datetime.now(UTC)).date().isoformat(),
            note=f"Tranche {settlement.tranche_no} sent to the hospital; "
            f"{format_money(settlement.amount_kes_minor, 'KES')}",
            request_id=f"invoice-payment-{settlement.id}",
        )
    except PayPalError as error:
        session.rollback()
        if error.retryable or error.code == "paypal_auth_failed":
            raise
        settlement = session.get(Settlement, settlement_id, with_for_update=True)
        if settlement is None:
            return "missing"
        settlement.invoice_payment_id = FAILED
        review_service.create_item(
            session,
            settlement.case,
            ReviewSource.ADMIN,
            "A tranche could not be recorded on the hospital's PayPal invoice",
            payload={
                "origin": "invoice_payment_failed",
                "settlement_id": str(settlement.id),
                "confirmation_id": str(settlement.confirmation_id),
                "paypal_issue": scrub(error.issue or error.code)[:100],
            },
        )
        session.commit()
        return "failed"
    settlement.invoice_payment_id = str(result.get("payment_id") or SKIPPED)[:40]
    session.commit()
    settlement = session.get(Settlement, settlement_id)
    assert settlement is not None
    try:
        _read_back(session, settlement.confirmation, client)
    except PayPalError as error:
        # The payment is recorded; the status is read again by the next sweep.
        session.rollback()
        logger.warning("could not read an invoice back", extra={"issue": error.issue})
        settlement = session.get(Settlement, settlement_id)
        assert settlement is not None
    audit_service.record(
        "invoice.payment_recorded",
        settlement,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={"case_id": settlement.case_id, "amount_usd_minor": amount},
        session=session,
    )
    session.commit()
    return "recorded"


# --------------------------------------------------------------------------- status


def _read_back(session: Session, confirmation: Confirmation, client: InvoicingGateway) -> None:
    """Ask PayPal where the invoice stands and keep the answer. The caller commits."""
    if confirmation.paypal_invoice_id is None:
        return
    status = client.get_invoice(confirmation.paypal_invoice_id).get("status")
    if isinstance(status, str) and status:
        confirmation.invoice_status = status[:24]
    session.commit()


def refresh_status(session: Session, confirmation_id: uuid.UUID) -> str | None:
    """Re-read one invoice's status from PayPal. Returns it, or None if there is no invoice."""
    confirmation = session.get(Confirmation, confirmation_id)
    if confirmation is None or confirmation.paypal_invoice_id is None:
        return None
    client = client_for(confirmation.hospital)
    if client is None:
        return confirmation.invoice_status
    _read_back(session, confirmation, client)
    return confirmation.invoice_status


def apply_reported_status(
    session: Session, invoice_id: str, status: str, *, request_id: str | None = None
) -> bool:
    """Take the status PayPal reported in a webhook. False if no confirmation has that invoice."""
    confirmation = session.scalar(
        select(Confirmation).where(Confirmation.paypal_invoice_id == invoice_id)
    )
    if confirmation is None:
        return False
    if confirmation.invoice_status != status[:24]:
        confirmation.invoice_status = status[:24]
        audit_service.record(
            "invoice.status_changed",
            confirmation,
            actor=ActorType.PAYPAL,
            request_id=request_id,
            details={"case_id": confirmation.case_id, "status": status[:24]},
        )
    return True


# --------------------------------------------------------------------------- queueing and sweep


def _enqueue(func: str, argument: str) -> None:
    try:
        jobs.enqueue(jobs.QUEUE_PAYMENTS, func, argument)
    except RedisError:
        logger.exception("could not queue an invoicing job; the sweep will retry")


def enqueue_issue(confirmation: Confirmation) -> None:
    """Queue the invoice for a bill that has just been confirmed. Call it after the commit."""
    if configured(confirmation.hospital):
        _enqueue("app.jobs.tasks.issue_invoice", str(confirmation.id))


def enqueue_payment(settlement: Settlement) -> None:
    """Queue the recording of a tranche that has just been paid. Call it after the commit."""
    if configured(settlement.confirmation.hospital):
        _enqueue("app.jobs.tasks.record_invoice_payment", str(settlement.id))


def _configured_codes() -> list[str]:
    from flask import current_app

    if not collector_email():
        return []
    return [
        code
        for code, credentials in current_app.config["HOSPITAL_PAYPAL_CREDENTIALS"].items()
        if credentials
    ]


def sweep(session: Session) -> int:
    """Finish whatever invoicing was missed: draft and send invoices, record paid tranches, and
    re-read the status of invoices still open. Returns how many things were done."""
    codes = _configured_codes()
    if not codes:
        return 0
    done = 0

    waiting = session.scalars(
        select(Confirmation.id)
        .join(Hospital, Confirmation.hospital_id == Hospital.id)
        .join(FacilityRegisterEntry, Hospital.register_entry_id == FacilityRegisterEntry.id)
        .where(
            FacilityRegisterEntry.register_code.in_(codes),
            Confirmation.status == ConfirmationStatus.CONFIRMED,
            (Confirmation.invoice_status.is_(None)) | (Confirmation.invoice_status == "DRAFT"),
        )
        .order_by(Confirmation.created_at)
    ).all()
    for confirmation_id in waiting:
        done += _quietly(lambda cid=confirmation_id: issue(session, cid) == "issued")

    unrecorded = session.scalars(
        select(Settlement.id)
        .join(Confirmation, Settlement.confirmation_id == Confirmation.id)
        .join(Hospital, Confirmation.hospital_id == Hospital.id)
        .join(FacilityRegisterEntry, Hospital.register_entry_id == FacilityRegisterEntry.id)
        .where(
            FacilityRegisterEntry.register_code.in_(codes),
            Settlement.status == SettlementStatus.SUCCEEDED,
            Settlement.invoice_payment_id.is_(None),
            Confirmation.paypal_invoice_id.is_not(None),
        )
        .order_by(Settlement.completed_at)
    ).all()
    for settlement_id in unrecorded:
        done += _quietly(lambda sid=settlement_id: record_payment(session, sid) == "recorded")

    open_invoices = session.scalars(
        select(Confirmation.id)
        .join(Hospital, Confirmation.hospital_id == Hospital.id)
        .join(FacilityRegisterEntry, Hospital.register_entry_id == FacilityRegisterEntry.id)
        .where(
            FacilityRegisterEntry.register_code.in_(codes),
            Confirmation.invoice_status.in_(("SENT", "UNPAID", "PARTIALLY_PAID", "SCHEDULED")),
        )
        .order_by(Confirmation.updated_at)
        .limit(SWEEP_REFRESH_LIMIT)
    ).all()
    for confirmation_id in open_invoices:
        done += _quietly(lambda cid=confirmation_id: refresh_status(session, cid) is not None)
    return done


def _quietly(action) -> int:  # type: ignore[no-untyped-def]
    """Run one step of the sweep; a failure is logged and the sweep goes on to the next."""
    try:
        return 1 if action() else 0
    except PayPalError as error:
        logger.warning("invoicing step failed", extra={"issue": error.issue or error.code})
        return 0
