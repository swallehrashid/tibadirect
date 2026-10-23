"""Reading PayPal's JSON into the few facts the services need.

Both the capture call and the ``PAYMENT.CAPTURE.COMPLETED`` webhook describe the same capture, in
slightly different envelopes; these functions turn either into one ``CaptureDetails``. Money is
converted from PayPal's decimal strings to integer cents here and nowhere else.
"""

from dataclasses import dataclass
from typing import Any

from app.money import MoneyError, to_minor
from app.payments import errors

CURRENCY = "USD"


@dataclass(frozen=True)
class CaptureDetails:
    """One PayPal capture. ``status`` is PayPal's own (COMPLETED, PENDING, DECLINED, ...)."""

    capture_id: str
    status: str
    gross_usd_minor: int
    fee_usd_minor: int
    net_usd_minor: int
    order_id: str | None = None
    custom_id: str | None = None
    payer_email: str | None = None

    @property
    def completed(self) -> bool:
        return self.status == "COMPLETED"


def amount_minor(amount: Any, what: str) -> int:
    """``{"currency_code": "USD", "value": "25.00"}`` to 2500."""
    if not isinstance(amount, dict):
        raise errors.malformed(f"{what} missing")
    currency = amount.get("currency_code") or amount.get("currency")
    if currency != CURRENCY:
        raise errors.malformed(f"{what} is not in {CURRENCY}")
    try:
        return to_minor(str(amount.get("value")))
    except MoneyError as exc:
        raise errors.malformed(f"{what} is not a number") from exc


def _capture(resource: Any, *, order_id: str | None, payer_email: str | None) -> CaptureDetails:
    if not isinstance(resource, dict) or not isinstance(resource.get("id"), str):
        raise errors.malformed("capture missing")
    gross = amount_minor(resource.get("amount"), "capture amount")
    breakdown = resource.get("seller_receivable_breakdown")
    if isinstance(breakdown, dict) and breakdown.get("paypal_fee") and breakdown.get("net_amount"):
        fee = amount_minor(breakdown["paypal_fee"], "PayPal fee")
        net = amount_minor(breakdown["net_amount"], "net amount")
    else:
        # PayPal leaves the breakdown out in some cases (for example while a payment is pending);
        # the donation then counts in full until the completed event brings the real figures.
        fee, net = 0, gross
    status = resource.get("status")
    return CaptureDetails(
        capture_id=resource["id"],
        status=status if isinstance(status, str) else "UNKNOWN",
        gross_usd_minor=gross,
        fee_usd_minor=fee,
        net_usd_minor=net,
        order_id=order_id,
        custom_id=resource.get("custom_id") if isinstance(resource.get("custom_id"), str) else None,
        payer_email=payer_email,
    )


def from_order(order: dict[str, Any]) -> CaptureDetails:
    """The first capture of an order, as returned by capture-order or get-order."""
    try:
        resource = order["purchase_units"][0]["payments"]["captures"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise errors.malformed("order has no capture") from exc
    payer = order.get("payer")
    email = payer.get("email_address") if isinstance(payer, dict) else None
    order_id = order.get("id") if isinstance(order.get("id"), str) else None
    return _capture(
        resource, order_id=order_id, payer_email=email if isinstance(email, str) else None
    )


def from_capture_resource(resource: dict[str, Any]) -> CaptureDetails:
    """The ``resource`` of a ``PAYMENT.CAPTURE.*`` webhook event."""
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    order_id = related.get("order_id") if isinstance(related, dict) else None
    return _capture(
        resource, order_id=order_id if isinstance(order_id, str) else None, payer_email=None
    )
