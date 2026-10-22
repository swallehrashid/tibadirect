"""PayPal webhooks: receive a delivery, check it is genuine, store it once, process it later.

``receive`` is what the HTTP route calls. It asks PayPal to verify the signature, stores the event
under its PayPal event ID (a replay changes nothing) and returns at once; a worker then runs
``process_event``, which hands the event to the handler for its type. Handlers are safe to run
twice and compare the event with what the database already knows, so a late, repeated or
out-of-order delivery never changes a balance twice.

A forged delivery is stored too, under a random ID, so the log shows the attempt without letting
a forger use up the ID of a real event that has not arrived yet.
"""

import contextlib
import dataclasses
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from flask import current_app
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.jobs import queue as jobs
from app.logging import scrub
from app.models.case import Case
from app.models.enums import (
    ActorType,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    ReviewSource,
    WebhookStatus,
)
from app.models.integration import WebhookEvent
from app.models.money import Donation, Settlement
from app.money import MoneyError
from app.payments import parsing
from app.payments.errors import PayPalError
from app.payments.paypal_client import get_client
from app.services import (
    audit_service,
    case_service,
    donation_service,
    ledger_service,
    refund_service,
    review_service,
    settlement_service,
)

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 256 * 1024
MOCK_WEBHOOK_ID = "MOCK-WEBHOOK-ID"
MAX_ERROR_CHARS = 500
# Keys that carry payer details; removed from the stored copy of every event.
PERSONAL_KEYS = frozenset(
    {
        "payer",
        "payment_source",
        "shipping",
        "email_address",
        "payer_email",
        "given_name",
        "surname",
        "full_name",
        "address",
        "phone_number",
        "receiver",
    }
)


class WebhookRejected(AppError):
    status_code = 400
    code = "webhook_rejected"
    message = "The webhook could not be accepted."


class WebhookDeferred(Exception):  # noqa: N818 - a signal to retry, not an error
    """The event arrived before the data it needs; try again shortly (the queue retries)."""


class Ignored(Exception):  # noqa: N818 - a normal outcome, not an error
    """The event is genuine but needs no action. The message says why."""


@dataclasses.dataclass(frozen=True)
class Received:
    """What ``receive`` decided: the stored event, and whether it was seen before."""

    event: WebhookEvent
    duplicate: bool


# --------------------------------------------------------------------------- receiving


def _webhook_id() -> str:
    config = current_app.config
    configured = config.get("PAYPAL_WEBHOOK_ID", "")
    if configured:
        return configured
    return MOCK_WEBHOOK_ID if config.get("PAYPAL_MODE") == "mock" else ""


def _minimise(value: Any) -> Any:
    """A copy of the event without payer details (the handlers do not need them)."""
    if isinstance(value, dict):
        return {k: _minimise(v) for k, v in value.items() if k not in PERSONAL_KEYS}
    if isinstance(value, list):
        return [_minimise(item) for item in value]
    return value


def _parse(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_BODY_BYTES:
        raise WebhookRejected(
            "The webhook is too large.", code="webhook_too_large", status_code=413
        )
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookRejected("The webhook body is not JSON.", code="webhook_invalid") from exc
    if (
        not isinstance(event, dict)
        or not isinstance(event.get("id"), str)
        or not isinstance(event.get("event_type"), str)
        or not 0 < len(event["id"]) <= 60
        or len(event["event_type"]) > 80
    ):
        raise WebhookRejected(
            "The webhook is missing its event ID or type.", code="webhook_invalid"
        )
    return event


def _store(session: Session, event: dict[str, Any], *, verified: bool) -> WebhookEvent:
    resource = event.get("resource")
    resource = resource if isinstance(resource, dict) else {}
    resource_id = resource.get("id")
    resource_type = event.get("resource_type")
    row = WebhookEvent(
        paypal_event_id=event["id"] if verified else f"unverified-{uuid.uuid4().hex}",
        event_type=event["event_type"],
        resource_type=resource_type[:40] if isinstance(resource_type, str) else None,
        resource_id=resource_id[:60] if isinstance(resource_id, str) else None,
        payload=_minimise(event),
        signature_verified=verified,
        status=WebhookStatus.RECEIVED if verified else WebhookStatus.IGNORED,
        error=None if verified else "The signature could not be verified",
    )
    session.add(row)
    session.flush()
    return row


def receive(session: Session, raw_body: bytes, headers: Mapping[str, str]) -> Received:
    """Verify and store one delivery. The caller commits, and enqueues processing if new.

    Raises ``WebhookRejected`` for a body that is not an event or a signature that does not
    verify (the forged attempt is stored first, so the caller must commit before re-raising), and
    ``PayPalError`` if PayPal could not be asked, so PayPal delivers the event again later.
    """
    event = _parse(raw_body)
    verified = get_client().verify_webhook_signature(
        headers=headers, raw_body=raw_body, webhook_id=_webhook_id()
    )
    if not verified:
        _store(session, event, verified=False)
        raise WebhookRejected(
            "The webhook signature could not be verified.", code="webhook_signature_invalid"
        )

    existing = session.execute(
        select(WebhookEvent).where(WebhookEvent.paypal_event_id == event["id"])
    ).scalar_one_or_none()
    if existing is not None:
        return Received(existing, duplicate=True)
    try:
        with session.begin_nested():
            row = _store(session, event, verified=True)
    except IntegrityError:
        # Two copies of the same delivery arrived together; the other one stored it.
        existing = session.execute(
            select(WebhookEvent).where(WebhookEvent.paypal_event_id == event["id"])
        ).scalar_one()
        return Received(existing, duplicate=True)
    return Received(row, duplicate=False)


# --------------------------------------------------------------------------- queueing

STALE_AFTER = timedelta(minutes=2)
STALE_BATCH = 100


def enqueue_processing(event_id: uuid.UUID) -> None:
    """Put an event on the payments queue. A failure is logged; the sweep tries again later."""
    try:
        jobs.enqueue(jobs.QUEUE_PAYMENTS, "app.jobs.tasks.process_webhook_event", str(event_id))
    except RedisError:
        logger.exception("could not queue a webhook event; the sweep will retry")


def enqueue_stale_events(session: Session) -> int:
    """Queue verified events still ``received`` after two minutes. Returns how many."""
    cutoff = datetime.now(UTC) - STALE_AFTER
    ids = session.execute(
        select(WebhookEvent.id)
        .where(
            WebhookEvent.status == WebhookStatus.RECEIVED,
            WebhookEvent.signature_verified.is_(True),
            WebhookEvent.received_at < cutoff,
        )
        .order_by(WebhookEvent.received_at)
        .limit(STALE_BATCH)
    ).scalars()
    queued = 0
    for event_id in list(ids):
        enqueue_processing(event_id)
        queued += 1
    return queued


# --------------------------------------------------------------------------- processing

Handler = Callable[[Session, dict[str, Any], WebhookEvent], None]


def _lock_donation(session: Session, resource: dict[str, Any]) -> tuple[Case, Donation]:
    """The donation a capture belongs to and its case, both locked (case first).

    Found by capture ID, then order ID, then the ``custom_id`` we set (the donation ID).
    """
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    lookups: list[Any] = []
    if isinstance(resource.get("id"), str):
        lookups.append(Donation.paypal_capture_id == resource["id"])
    if isinstance(related.get("order_id"), str):
        lookups.append(Donation.paypal_order_id == related["order_id"])
    custom = resource.get("custom_id")
    if isinstance(custom, str):
        with contextlib.suppress(ValueError):
            lookups.append(Donation.id == uuid.UUID(custom))
    for condition in lookups:
        found = session.execute(select(Donation.id, Donation.case_id).where(condition)).first()
        if found is None:
            continue
        case = case_service.lock_case(session, found.case_id)
        donation = session.execute(
            select(Donation)
            .where(Donation.id == found.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        return case, donation
    raise Ignored("No donation matches this payment")


def _handle_order_approved(session: Session, resource: dict[str, Any], event: WebhookEvent) -> None:
    """The donor approved the order in PayPal. Capture happens when the browser calls us, or when
    the completed event arrives, so there is nothing to do here."""
    raise Ignored("Approval needs no action; the capture event completes the donation")


def _handle_capture_completed(
    session: Session, resource: dict[str, Any], event: WebhookEvent
) -> None:
    case, donation = _lock_donation(session, resource)
    details = parsing.from_capture_resource(resource)
    if not details.completed:
        raise Ignored(f"The capture is {details.status.lower()}, not completed")
    if details.custom_id and details.custom_id != str(donation.id):
        raise Ignored("The payment's custom ID does not match the donation found")
    if donation.status in donation_service.TAKEN_STATUSES:
        return
    if details.order_id:
        try:
            order = get_client().get_order(details.order_id)
            email = parsing.from_order(order).payer_email
            details = dataclasses.replace(details, payer_email=email)
        except (
            PayPalError,
            MoneyError,
        ):  # best effort: the receipt email needs the payer's address
            logger.warning("webhook: could not read the payer for a receipt")
    donation_service.record_capture(
        session, donation, case, details, actor=ActorType.PAYPAL, request_id=_event_request(event)
    )


def _handle_capture_denied(session: Session, resource: dict[str, Any], event: WebhookEvent) -> None:
    _, donation = _lock_donation(session, resource)
    if donation.status != DonationStatus.CREATED:
        raise Ignored("The donation is not waiting for a payment")
    donation.status = DonationStatus.DENIED
    audit_service.record(
        "donation.denied",
        donation,
        actor=ActorType.PAYPAL,
        request_id=_event_request(event),
        details={"case_id": donation.case_id, "paypal_status": "DENIED"},
    )


def _capture_id_from_links(resource: dict[str, Any]) -> str | None:
    """A refund names the capture it came from in an ``up`` link ending in the capture ID."""
    for link in resource.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "up" and isinstance(link.get("href"), str):
            return link["href"].rstrip("/").rsplit("/", 1)[-1]
    return None


def _handle_capture_refunded(
    session: Session, resource: dict[str, Any], event: WebhookEvent
) -> None:
    refund_id = resource.get("id")
    if not isinstance(refund_id, str):
        raise Ignored("The refund has no ID")
    status = str(resource.get("status", "")).upper()
    if status != "COMPLETED":
        raise Ignored(f"The refund is {status.lower() or 'of unknown status'}, not completed")
    capture_id = _capture_id_from_links(resource)
    if capture_id is None:
        raise Ignored("The refund does not name its payment")
    case, donation = _lock_donation(session, {"id": capture_id})
    amount = parsing.amount_minor(resource.get("amount"), "refund amount")

    known = refund_service.find_by_paypal_id(session, refund_id)
    if known is not None:
        refund_service.complete_refund(
            session,
            known,
            case,
            paypal_refund_id=refund_id,
            actor=ActorType.PAYPAL,
            request_id=_event_request(event),
        )
        return
    pending = refund_service.pending_for(session, donation)
    matching = next((r for r in pending if r.amount_usd_minor == amount), None)
    if matching is not None:
        refund_service.complete_refund(
            session,
            matching,
            case,
            paypal_refund_id=refund_id,
            actor=ActorType.PAYPAL,
            request_id=_event_request(event),
        )
    elif pending:
        # We asked for a refund that has not been written down yet; wait for it, then retry.
        raise WebhookDeferred("A refund request for this payment is still being recorded")
    else:
        refund_service.record_external_refund(
            session,
            donation,
            case,
            paypal_refund_id=refund_id,
            amount_usd_minor=amount,
            request_id=_event_request(event),
        )


def _handle_capture_reversed(
    session: Session, resource: dict[str, Any], event: WebhookEvent
) -> None:
    case, donation = _lock_donation(session, resource)
    if donation.status == DonationStatus.REVERSED:
        return
    if donation.status not in donation_service.TAKEN_STATUSES:
        raise Ignored("The payment was never recorded as taken")
    held = max(
        0, donation.net_usd_minor - donation.allocated_usd_minor - donation.refunded_usd_minor
    )
    donation.status = DonationStatus.REVERSED
    if held:
        ledger_service.append(
            session,
            [
                ledger_service.NewEntry(
                    case.id,
                    LedgerEntryType.REVERSAL,
                    LedgerAccount.CASE_FUNDS,
                    -held,
                    "USD",
                    "donation",
                    donation.id,
                )
            ],
        )
    session.expire(case, ["donations"])
    case_service.recompute_totals(case)
    case_service.sync_funding(case)
    audit_service.record(
        "donation.reversed",
        donation,
        actor=ActorType.PAYPAL,
        request_id=_event_request(event),
        details={
            "case_id": case.id,
            "removed_from_case_usd_minor": held,
            "already_paid_out_usd_minor": donation.allocated_usd_minor,
        },
    )
    review_service.create_item(
        session,
        case,
        ReviewSource.ADMIN,
        "A donation was reversed by PayPal (dispute or chargeback)",
        payload={
            "donation_id": str(donation.id),
            "removed_from_case_usd_minor": held,
            "already_paid_out_usd_minor": donation.allocated_usd_minor,
        },
    )


def _handle_payout(session: Session, resource: dict[str, Any], event: WebhookEvent) -> None:
    """A payout changed. The event only says which one; the provider is asked for its state, so
    a forged or out-of-order event can never set a status by itself."""
    header = resource.get("batch_header") or {}
    batch_id = resource.get("payout_batch_id") or header.get("payout_batch_id")
    sender_batch = resource.get("sender_batch_id") or (header.get("sender_batch_header") or {}).get(
        "sender_batch_id"
    )
    conditions = []
    if isinstance(batch_id, str):
        conditions.append(Settlement.external_batch_id == batch_id)
    if isinstance(sender_batch, str):
        conditions.append(Settlement.idempotency_key == sender_batch)
    settlement_id = None
    for condition in conditions:
        settlement_id = session.execute(select(Settlement.id).where(condition)).scalar_one_or_none()
        if settlement_id is not None:
            break
    if settlement_id is None:
        raise Ignored("No settlement matches this payout")
    settlement_service.poll_settlement(
        session, settlement_id, include_finished=True, request_id=_event_request(event)
    )


def _handle_later(session: Session, resource: dict[str, Any], event: WebhookEvent) -> None:
    raise Ignored("Invoices are handled from P3-F17 onwards")


def _event_request(event: WebhookEvent) -> str:
    return f"webhook-{event.paypal_event_id}"[:64]


HANDLERS: dict[str, Handler] = {
    "CHECKOUT.ORDER.APPROVED": _handle_order_approved,
    "PAYMENT.CAPTURE.COMPLETED": _handle_capture_completed,
    "PAYMENT.CAPTURE.DENIED": _handle_capture_denied,
    "PAYMENT.CAPTURE.REFUNDED": _handle_capture_refunded,
    "PAYMENT.CAPTURE.REVERSED": _handle_capture_reversed,
    "PAYMENT.PAYOUTSBATCH.SUCCESS": _handle_payout,
    "PAYMENT.PAYOUTSBATCH.DENIED": _handle_payout,
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED": _handle_payout,
    "PAYMENT.PAYOUTS-ITEM.FAILED": _handle_payout,
    "PAYMENT.PAYOUTS-ITEM.BLOCKED": _handle_payout,
    "PAYMENT.PAYOUTS-ITEM.RETURNED": _handle_payout,
    "INVOICING.INVOICE.PAID": _handle_later,
}


def _finish(
    session: Session, event_id: uuid.UUID, status: WebhookStatus, error: str | None
) -> None:
    row = session.get(WebhookEvent, event_id)
    if row is not None:
        row.status = status
        row.error = scrub(error)[:MAX_ERROR_CHARS] if error else None
        row.processed_at = datetime.now(UTC)
        session.commit()


def process_event(session: Session, event_id: uuid.UUID) -> WebhookStatus | None:
    """Run the handler for a stored event and record the outcome. Safe to run twice.

    A handler that raises leaves the event ``failed`` (with a scrubbed reason) and re-raises, so
    the queue retries it; a final status (processed, ignored) is never changed.
    """
    event = session.execute(
        select(WebhookEvent)
        .where(WebhookEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if event is None or not event.signature_verified:
        session.rollback()
        return None
    if event.status in (WebhookStatus.PROCESSED, WebhookStatus.IGNORED):
        session.rollback()
        return event.status

    handler = HANDLERS.get(event.event_type)
    resource = event.payload.get("resource") if isinstance(event.payload, dict) else None
    try:
        if handler is None:
            raise Ignored("No handler for this event type")
        handler(session, resource if isinstance(resource, dict) else {}, event)
    except Ignored as ignored:
        session.rollback()
        _finish(session, event_id, WebhookStatus.IGNORED, str(ignored))
        return WebhookStatus.IGNORED
    except Exception as exc:
        session.rollback()
        reason = "Waiting for related data" if isinstance(exc, WebhookDeferred) else repr(exc)
        _finish(session, event_id, WebhookStatus.FAILED, reason)
        raise
    # Handlers may already have committed their own work; this settles the event itself.
    _finish(session, event_id, WebhookStatus.PROCESSED, None)
    return WebhookStatus.PROCESSED
