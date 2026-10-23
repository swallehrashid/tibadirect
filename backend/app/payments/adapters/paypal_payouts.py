"""PayPal Payouts to the hospital's PayPal account.

In the sandbox this stands in for a licensed Kenyan settlement partner. The batch ID sent to PayPal
is the settlement's idempotency key, so repeating a send can never pay twice.
"""

from typing import Any

from app.models.enums import SettlementMethod
from app.models.hospital import Hospital
from app.models.money import Settlement
from app.payments.adapters.base import AdapterResult
from app.payments.paypal_client import PayoutItem, get_client

EMAIL_SUBJECT = "TibaDirect settlement"
# PayPal item states that are not final: the money is on its way or waiting for the receiver.
IN_PROGRESS_ITEM_STATES = frozenset({"PENDING", "ONHOLD", "UNCLAIMED"})
FAILED_ITEM_STATES = frozenset({"FAILED", "BLOCKED", "RETURNED", "REFUNDED", "REVERSED"})
FAILED_BATCH_STATES = frozenset({"DENIED", "CANCELED"})


def sender_item_id(settlement: Settlement) -> str:
    """The settlement's ID cut to the 30 characters PayPal allows for a sender item ID."""
    return settlement.id.hex[:30]


class PayPalPayoutsAdapter:
    method = SettlementMethod.PAYPAL_PAYOUTS

    def check_ready(self, hospital: Hospital) -> str | None:
        if not hospital.paypal_payout_email:
            return "The hospital has no PayPal payout email on file"
        return None

    def send(self, settlement: Settlement, hospital: Hospital) -> AdapterResult:
        invoice = settlement.confirmation.invoice_number or "n/a"
        response = get_client().create_payout(
            sender_batch_id=settlement.idempotency_key,
            items=[
                PayoutItem(
                    receiver_email=hospital.paypal_payout_email or "",
                    amount_usd_minor=settlement.amount_usd_minor,
                    sender_item_id=sender_item_id(settlement),
                    note=f"TibaDirect tranche {settlement.tranche_no} for invoice {invoice}",
                )
            ],
            email_subject=EMAIL_SUBJECT,
            request_id=settlement.idempotency_key,
        )
        header = response.get("batch_header") or {}
        return AdapterResult("processing", batch_id=header.get("payout_batch_id"))

    def poll(self, settlement: Settlement) -> AdapterResult:
        if not settlement.external_batch_id:
            return AdapterResult("processing")
        return interpret(get_client().get_payout(settlement.external_batch_id))


def interpret(response: dict[str, Any]) -> AdapterResult:
    """Turn PayPal's payout batch into one answer about our single-item batch."""
    header = response.get("batch_header") or {}
    batch_id = header.get("payout_batch_id")
    batch_status = str(header.get("batch_status", "")).upper()
    if batch_status in FAILED_BATCH_STATES:
        return AdapterResult("failed", batch_id=batch_id, failure=f"batch {batch_status.lower()}")
    items = response.get("items") or []
    if not items:
        return AdapterResult("processing", batch_id=batch_id)
    item = items[0]
    state = str(item.get("transaction_status", "")).upper()
    item_id = item.get("payout_item_id")
    if state == "SUCCESS":
        return AdapterResult("succeeded", batch_id=batch_id, item_id=item_id)
    if state in FAILED_ITEM_STATES:
        reason = (item.get("errors") or {}).get("name") or state
        return AdapterResult(
            "failed", batch_id=batch_id, item_id=item_id, failure=str(reason).lower()
        )
    return AdapterResult("processing", batch_id=batch_id, item_id=item_id)
