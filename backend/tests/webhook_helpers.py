"""Build PayPal webhook events and deliver them the way PayPal does."""

import json
import uuid
from typing import Any

from app.models.money import Donation
from app.payments.paypal_mock import MockPayPal, signed_webhook_headers

WEBHOOK_ID = "WH-TEST-WEBHOOK-ID"  # TestConfig.PAYPAL_WEBHOOK_ID


def make_event(event_type: str, resource: dict[str, Any], event_id: str | None = None) -> dict:
    return {
        "id": event_id or f"WH-{uuid.uuid4().hex[:20].upper()}",
        "event_version": "1.0",
        "create_time": "2026-10-23T09:00:00.000Z",
        "resource_type": event_type.split(".")[1].lower(),
        "event_type": event_type,
        "summary": "A test event",
        "resource": resource,
    }


def deliver(
    client,  # type: ignore[no-untyped-def]
    event: dict[str, Any] | None = None,
    *,
    raw: bytes | None = None,
    sign: bool = True,
    headers: dict[str, str] | None = None,
    remote_addr: str = "203.0.113.7",
):
    """POST an event to the webhook route, signed for the mock unless told otherwise."""
    body = raw if raw is not None else json.dumps(event).encode()
    sent = signed_webhook_headers(WEBHOOK_ID, body) if sign else {}
    sent.update(headers or {})
    return client.post(
        "/api/webhooks/paypal",
        data=body,
        content_type="application/json",
        headers=sent,
        environ_overrides={"REMOTE_ADDR": remote_addr},
    )


def paypal_took_the_money(paypal: MockPayPal, donation: Donation) -> dict[str, Any]:
    """The donor paid but our capture call never came back: PayPal captured it on its own.

    Returns the capture as PayPal's PAYMENT.CAPTURE.COMPLETED event carries it.
    """
    order = paypal.capture_order(donation.paypal_order_id, request_id="paypal-side-capture")
    capture = order["purchase_units"][0]["payments"]["captures"][0]
    return {
        **capture,
        "supplementary_data": {"related_ids": {"order_id": donation.paypal_order_id}},
    }


def refund_resource(donation: Donation, refund_id: str, minor: int, status: str = "COMPLETED"):  # type: ignore[no-untyped-def]
    value = f"{minor // 100}.{minor % 100:02d}"
    return {
        "id": refund_id,
        "status": status,
        "amount": {"currency_code": "USD", "value": value},
        "links": [
            {
                "href": f"https://api.sandbox.paypal.com/v2/payments/refunds/{refund_id}",
                "rel": "self",
            },
            {
                "href": (
                    "https://api.sandbox.paypal.com/v2/payments/captures/"
                    f"{donation.paypal_capture_id}"
                ),
                "rel": "up",
            },
        ],
    }
