"""An offline stand-in for PayPal with the same methods and JSON shapes as ``PayPalClient``.

Used by tests, the seed data and offline demos (``PAYPAL_MODE=mock``); the hackathon demo uses the
sandbox. IDs are derived from the request ID, so a repeated request returns the same object, and
the fee is a configurable percentage plus a fixed amount (2.9 % + $0.30 by default).

State lives in a store: in memory (one process, used by tests) or in Redis (shared by the API and
worker processes, the default outside tests). Tests steer the fake with ``approve``,
``fail_next`` and ``next_payout_outcome``.
"""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP
from typing import Any, Protocol

from redis.exceptions import RedisError

from app.extensions import get_redis
from app.money import apply_bps, format_amount, to_minor
from app.payments import errors
from app.payments.paypal_client import CURRENCY, PayoutItem

KEY_PREFIX = "tibadirect:paypal_mock:"
MOCK_PAYER_EMAIL = "mock.donor@example.test"
WEBHOOK_SIGNATURE_PREFIX = "MOCKSIG-"

WEBHOOK_HEADERS = (
    "PAYPAL-AUTH-ALGO",
    "PAYPAL-CERT-URL",
    "PAYPAL-TRANSMISSION-ID",
    "PAYPAL-TRANSMISSION-SIG",
    "PAYPAL-TRANSMISSION-TIME",
)
PAYOUT_OUTCOMES = frozenset({"SUCCESS", "FAILED", "BLOCKED", "RETURNED", "PENDING"})
CAPTURE_STATUSES = frozenset({"COMPLETED", "PENDING", "DECLINED"})


class Store(Protocol):
    def get(self, kind: str, key: str) -> dict[str, Any] | None: ...
    def put(self, kind: str, key: str, value: dict[str, Any]) -> None: ...
    def clear(self) -> None: ...


class MemoryStore:
    """Process-local state. A fresh store per ``MockPayPal`` keeps tests apart."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        raw = self._data.get((kind, key))
        return json.loads(raw) if raw is not None else None

    def put(self, kind: str, key: str, value: dict[str, Any]) -> None:
        self._data[(kind, key)] = json.dumps(value)

    def clear(self) -> None:
        self._data.clear()


class RedisStore:
    """State shared between processes through the application's Redis."""

    TTL_SECONDS = 7 * 24 * 3600

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        raw = get_redis().get(f"{KEY_PREFIX}{kind}:{key}")
        return json.loads(raw) if raw else None

    def put(self, kind: str, key: str, value: dict[str, Any]) -> None:
        get_redis().set(f"{KEY_PREFIX}{kind}:{key}", json.dumps(value), ex=self.TTL_SECONDS)

    def clear(self) -> None:
        client = get_redis()
        try:
            for key in client.scan_iter(f"{KEY_PREFIX}*"):
                client.delete(key)
        except RedisError:
            pass


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derive(prefix: str, seed: str) -> str:
    """A stable, PayPal-looking ID: the same seed always gives the same ID."""
    return f"MOCK{prefix}{hashlib.sha1(seed.encode()).hexdigest()[:14].upper()}"


def webhook_signature(webhook_id: str, transmission_id: str, raw_body: bytes) -> str:
    """The signature the fake accepts for a delivery. Tests use it to sign their own events."""
    digest = hashlib.sha256(
        webhook_id.encode() + b"|" + transmission_id.encode() + b"|" + raw_body
    ).hexdigest()
    return WEBHOOK_SIGNATURE_PREFIX + digest[:32]


def signed_webhook_headers(webhook_id: str, raw_body: bytes) -> dict[str, str]:
    """Headers for a delivery of ``raw_body`` that the fake will verify."""
    transmission_id = str(uuid.uuid4())
    return {
        "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        "PAYPAL-CERT-URL": "https://api-m.sandbox.paypal.com/v1/notifications/certs/CERT-MOCK",
        "PAYPAL-TRANSMISSION-ID": transmission_id,
        "PAYPAL-TRANSMISSION-SIG": webhook_signature(webhook_id, transmission_id, raw_body),
        "PAYPAL-TRANSMISSION-TIME": _stamp(),
    }


class MockPayPal:
    """The fake. Methods match ``PayPalGateway``; extra helpers steer it from tests."""

    def __init__(
        self,
        *,
        store: Store | None = None,
        fee_bps: int = 290,
        fee_fixed_usd_minor: int = 30,
        auto_approve: bool = True,
        payout_delay_seconds: float = 0.0,
    ) -> None:
        self.store: Store = store or MemoryStore()
        self.fee_bps = fee_bps
        self.fee_fixed_usd_minor = fee_fixed_usd_minor
        self.auto_approve = auto_approve
        self.payout_delay_seconds = payout_delay_seconds

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "MockPayPal":
        shared = config.get("PAYPAL_MOCK_STORE", "redis") == "redis"
        return cls(
            store=RedisStore() if shared else MemoryStore(),
            fee_bps=config.get("PAYPAL_MOCK_FEE_BPS", 290),
            fee_fixed_usd_minor=config.get("PAYPAL_MOCK_FEE_FIXED_USD_MINOR", 30),
            auto_approve=config.get("PAYPAL_MOCK_AUTO_APPROVE", True),
            payout_delay_seconds=config.get("PAYPAL_MOCK_PAYOUT_DELAY_SECONDS", 0.0),
        )

    # ------------------------------------------------------------------ steering (tests, demos)

    def approve(
        self, order_id: str, *, payer_email: str = MOCK_PAYER_EMAIL, payer_name: str = "Mock Donor"
    ) -> None:
        """Do what the donor does in the PayPal window: approve the order."""
        order = self._order(order_id)
        given, _, family = payer_name.partition(" ")
        order["status"] = "APPROVED"
        order["payer"] = {
            "email_address": payer_email,
            "payer_id": _derive("PAYER", payer_email),
            "name": {"given_name": given, "surname": family},
        }
        self.store.put("order", order_id, order)

    def fail_next(
        self,
        operation: str,
        *,
        http_status: int = 422,
        name: str = "UNPROCESSABLE_ENTITY",
        issue: str | None = None,
    ) -> None:
        """Make the next call to ``operation`` (for example ``capture_order``) fail like PayPal."""
        queue = self.store.get("faults", operation) or {"items": []}
        queue["items"].append({"http_status": http_status, "name": name, "issue": issue})
        self.store.put("faults", operation, queue)

    def next_capture_status(self, status: str) -> None:
        """Make the next capture end as PENDING or DECLINED instead of COMPLETED."""
        if status not in CAPTURE_STATUSES:
            raise ValueError(f"Unknown capture status {status!r}")
        queue = self.store.get("outcomes", "capture") or {"items": []}
        queue["items"].append(status)
        self.store.put("outcomes", "capture", queue)

    def next_payout_outcome(self, outcome: str) -> None:
        """Decide how the next payout item ends: SUCCESS, FAILED, BLOCKED, RETURNED or PENDING."""
        if outcome not in PAYOUT_OUTCOMES:
            raise ValueError(f"Unknown payout outcome {outcome!r}")
        queue = self.store.get("outcomes", "payout") or {"items": []}
        queue["items"].append(outcome)
        self.store.put("outcomes", "payout", queue)

    def reset(self) -> None:
        self.store.clear()

    def _maybe_fail(self, operation: str) -> None:
        queue = self.store.get("faults", operation)
        if not queue or not queue["items"]:
            return
        fault = queue["items"].pop(0)
        self.store.put("faults", operation, queue)
        body: dict[str, Any] = {"name": fault["name"], "debug_id": "mockdebugid"}
        if fault["issue"]:
            body["details"] = [{"issue": fault["issue"]}]
        raise errors.from_response(fault["http_status"], body, "mockdebugid")

    # ------------------------------------------------------------------ orders

    def _order(self, order_id: str) -> dict[str, Any]:
        order = self.store.get("order", order_id)
        if order is None:
            raise errors.from_response(
                404,
                {"name": "RESOURCE_NOT_FOUND", "details": [{"issue": "INVALID_RESOURCE_ID"}]},
                None,
            )
        return order

    def create_order(
        self,
        *,
        amount_usd_minor: int,
        reference_id: str,
        custom_id: str,
        description: str,
        request_id: str,
    ) -> dict[str, Any]:
        self._maybe_fail("create_order")
        order_id = _derive("ORD", request_id)
        existing = self.store.get("order", order_id)
        if existing is not None:
            return existing
        order = {
            "id": order_id,
            "intent": "CAPTURE",
            "status": "CREATED",
            "create_time": _stamp(),
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "custom_id": custom_id,
                    "description": description,
                    "amount": {"currency_code": CURRENCY, "value": format_amount(amount_usd_minor)},
                }
            ],
            "links": [
                {
                    "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                    "rel": "approve",
                    "method": "GET",
                }
            ],
        }
        self.store.put("order", order_id, order)
        return order

    def get_order(self, order_id: str) -> dict[str, Any]:
        self._maybe_fail("get_order")
        return self._order(order_id)

    def capture_order(self, order_id: str, *, request_id: str) -> dict[str, Any]:
        self._maybe_fail("capture_order")
        order = self._order(order_id)
        replay = self.store.get("capture_request", request_id)
        if replay is not None:
            return replay
        if order["status"] == "COMPLETED":
            raise errors.from_response(
                422,
                {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_ALREADY_CAPTURED"}]},
                None,
            )
        if order["status"] != "APPROVED":
            if not self.auto_approve:
                raise errors.from_response(
                    422,
                    {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]},
                    None,
                )
            self.approve(order_id)
            order = self._order(order_id)

        unit = order["purchase_units"][0]
        gross = to_minor(unit["amount"]["value"])
        fee = apply_bps(gross, self.fee_bps, rounding=ROUND_HALF_UP) + self.fee_fixed_usd_minor
        fee = min(fee, gross)
        queue = self.store.get("outcomes", "capture") or {"items": []}
        status = queue["items"].pop(0) if queue["items"] else "COMPLETED"
        self.store.put("outcomes", "capture", queue)
        capture_id = _derive("CAP", f"{order_id}|capture")
        capture: dict[str, Any] = {
            "id": capture_id,
            "status": status,
            "amount": unit["amount"],
            "final_capture": True,
            "custom_id": unit["custom_id"],
            "create_time": _stamp(),
            "update_time": _stamp(),
        }
        if status == "COMPLETED":
            capture["seller_receivable_breakdown"] = {
                "gross_amount": _money(gross),
                "paypal_fee": _money(fee),
                "net_amount": _money(gross - fee),
            }
        else:
            capture["status_details"] = {
                "reason": "PENDING_REVIEW" if status == "PENDING" else "OTHER"
            }
        unit["payments"] = {"captures": [capture]}
        if status == "COMPLETED":
            order["status"] = "COMPLETED"
        completed = {
            "id": order_id,
            "status": order["status"],
            "payer": order["payer"],
            "purchase_units": [
                {"reference_id": unit["reference_id"], "payments": {"captures": [capture]}}
            ],
        }
        self.store.put("order", order_id, order)
        self.store.put(
            "capture",
            capture_id,
            {"order_id": order_id, "gross": gross, "refunded": 0, "payer": order["payer"]},
        )
        self.store.put("capture_request", request_id, completed)
        return completed

    # ------------------------------------------------------------------ refunds

    def refund_capture(
        self, capture_id: str, *, amount_usd_minor: int, note: str, request_id: str
    ) -> dict[str, Any]:
        self._maybe_fail("refund_capture")
        replay = self.store.get("refund_request", request_id)
        if replay is not None:
            return replay
        capture = self.store.get("capture", capture_id)
        if capture is None:
            raise errors.from_response(
                404,
                {"name": "RESOURCE_NOT_FOUND", "details": [{"issue": "INVALID_RESOURCE_ID"}]},
                None,
            )
        remaining = capture["gross"] - capture["refunded"]
        if remaining <= 0:
            raise errors.from_response(
                422,
                {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "CAPTURE_FULLY_REFUNDED"}]},
                None,
            )
        if amount_usd_minor > remaining:
            raise errors.from_response(
                422,
                {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "REFUND_AMOUNT_EXCEEDED"}],
                },
                None,
            )
        capture["refunded"] += amount_usd_minor
        self.store.put("capture", capture_id, capture)
        refund = {
            "id": _derive("REF", request_id),
            "status": "COMPLETED",
            "amount": _money(amount_usd_minor),
            "note_to_payer": note,
            "create_time": _stamp(),
            "update_time": _stamp(),
        }
        self.store.put("refund_request", request_id, refund)
        return refund

    # ------------------------------------------------------------------ payouts

    def create_payout(
        self,
        *,
        sender_batch_id: str,
        items: list[PayoutItem],
        email_subject: str,
        request_id: str,
    ) -> dict[str, Any]:
        self._maybe_fail("create_payout")
        replay = self.store.get("payout_request", request_id)
        if replay is not None:
            return replay
        if self.store.get("payout_sender", sender_batch_id) is not None:
            raise errors.from_response(
                400,
                {
                    "name": "USER_BUSINESS_ERROR",
                    "message": "Batch with given sender_batch_id already exists",
                },
                None,
            )
        batch_id = _derive("BATCH", sender_batch_id)
        queue = self.store.get("outcomes", "payout") or {"items": []}
        outcome = queue["items"].pop(0) if queue["items"] else "SUCCESS"
        self.store.put("outcomes", "payout", queue)
        total = sum(item.amount_usd_minor for item in items)
        header = {
            "payout_batch_id": batch_id,
            "batch_status": "PENDING",
            "sender_batch_header": {
                "sender_batch_id": sender_batch_id,
                "email_subject": email_subject,
            },
        }
        batch = {
            "header": header,
            "outcome": outcome,
            "created": time.time(),
            "total": total,
            "items": [
                {
                    "payout_item_id": _derive("ITEM", f"{batch_id}|{item.sender_item_id}"),
                    "transaction_id": _derive("TXN", f"{batch_id}|{item.sender_item_id}"),
                    "payout_batch_id": batch_id,
                    "payout_item": {
                        "recipient_type": "EMAIL",
                        "receiver": item.receiver_email,
                        "amount": {
                            "currency": CURRENCY,
                            "value": format_amount(item.amount_usd_minor),
                        },
                        "sender_item_id": item.sender_item_id,
                        "note": item.note,
                    },
                }
                for item in items
            ],
        }
        self.store.put("payout", batch_id, batch)
        self.store.put("payout_sender", sender_batch_id, {"batch_id": batch_id})
        response = {"batch_header": header}
        self.store.put("payout_request", request_id, response)
        return response

    def get_payout(self, batch_id: str) -> dict[str, Any]:
        self._maybe_fail("get_payout")
        batch = self.store.get("payout", batch_id)
        if batch is None:
            raise errors.from_response(
                404,
                {"name": "RESOURCE_NOT_FOUND", "details": [{"issue": "INVALID_RESOURCE_ID"}]},
                None,
            )
        header = dict(batch["header"])
        ready = time.time() - batch["created"] >= self.payout_delay_seconds
        if batch["outcome"] == "PENDING" or not ready:
            return {"batch_header": header, "items": []}
        header["batch_status"] = "SUCCESS"
        header["amount"] = _money(batch["total"], key="currency")
        status = {
            "SUCCESS": "SUCCESS",
            "FAILED": "FAILED",
            "BLOCKED": "BLOCKED",
            "RETURNED": "RETURNED",
        }
        items = []
        for item in batch["items"]:
            entry = {
                **item,
                "transaction_status": status[batch["outcome"]],
                "time_processed": _stamp(),
            }
            if batch["outcome"] == "FAILED":
                entry["errors"] = {
                    "name": "RECEIVER_UNREGISTERED",
                    "message": "Receiver is unregistered",
                }
            items.append(entry)
        return {"batch_header": header, "items": items}

    # ------------------------------------------------------------------ invoices

    def _invoice(self, invoice_id: str) -> dict[str, Any]:
        invoice = self.store.get("invoice", invoice_id)
        if invoice is None:
            raise errors.from_response(
                404,
                {"name": "RESOURCE_NOT_FOUND", "details": [{"issue": "INVALID_RESOURCE_ID"}]},
                None,
            )
        return invoice

    @staticmethod
    def _wrong_status(invoice: dict[str, Any]) -> errors.PayPalError:
        return errors.from_response(
            422,
            {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "INVALID_INVOICE_STATUS"}]},
            None,
        )

    def create_invoice(
        self,
        *,
        invoice_number: str,
        recipient_email: str,
        item_name: str,
        amount_usd_minor: int,
        note: str,
        invoice_date: str,
        request_id: str,
    ) -> dict[str, Any]:
        self._maybe_fail("create_invoice")
        replay = self.store.get("invoice_request", request_id)
        if replay is not None:
            return replay
        if self.store.get("invoice_number", invoice_number) is not None:
            raise errors.from_response(
                422,
                {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "DUPLICATE_INVOICE_NUMBER"}],
                },
                None,
            )
        digest = hashlib.sha1(request_id.encode()).hexdigest()[:16].upper()
        invoice_id = "INV2-" + "-".join(digest[i : i + 4] for i in range(0, 16, 4))
        invoice = {
            "id": invoice_id,
            "status": "DRAFT",
            "detail": {
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "currency_code": CURRENCY,
                "note": note,
            },
            "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
            "items": [
                {
                    "name": item_name,
                    "quantity": "1",
                    "unit_amount": _money(amount_usd_minor),
                }
            ],
            "total_minor": amount_usd_minor,
            "amount": _money(amount_usd_minor),
            "due_amount": _money(amount_usd_minor),
            "payments": {"paid_amount": _money(0), "transactions": []},
        }
        self.store.put("invoice", invoice_id, invoice)
        self.store.put("invoice_number", invoice_number, {"id": invoice_id})
        link = {
            "rel": "self",
            "href": f"https://api.sandbox.paypal.com/v2/invoicing/invoices/{invoice_id}",
            "method": "GET",
        }
        self.store.put("invoice_request", request_id, link)
        return link

    def send_invoice(self, invoice_id: str, *, request_id: str) -> dict[str, Any]:
        self._maybe_fail("send_invoice")
        invoice = self._invoice(invoice_id)
        if invoice["status"] in ("CANCELLED", "PAID"):
            raise self._wrong_status(invoice)
        if invoice["status"] == "DRAFT":
            invoice["status"] = "SENT"
            self.store.put("invoice", invoice_id, invoice)
        return {
            "rel": "x-recipient-view",
            "href": f"https://www.sandbox.paypal.com/invoice/p/#{invoice_id}",
            "method": "GET",
        }

    def record_invoice_payment(
        self,
        invoice_id: str,
        *,
        amount_usd_minor: int,
        payment_date: str,
        note: str,
        request_id: str,
    ) -> dict[str, Any]:
        self._maybe_fail("record_invoice_payment")
        replay = self.store.get("invoice_payment_request", request_id)
        if replay is not None:
            return replay
        invoice = self._invoice(invoice_id)
        if invoice["status"] in ("DRAFT", "CANCELLED", "PAID"):
            raise self._wrong_status(invoice)
        paid = sum(to_minor(t["amount"]["value"]) for t in invoice["payments"]["transactions"])
        if paid + amount_usd_minor > invoice["total_minor"]:
            raise errors.from_response(
                422,
                {
                    "name": "UNPROCESSABLE_ENTITY",
                    "details": [{"issue": "PAYMENT_AMOUNT_EXCEEDS_DUE_AMOUNT"}],
                },
                None,
            )
        payment_id = _derive("INVPAY", request_id)
        invoice["payments"]["transactions"].append(
            {
                "payment_id": payment_id,
                "method": "BANK_TRANSFER",
                "payment_date": payment_date,
                "note": note,
                "amount": _money(amount_usd_minor),
            }
        )
        paid += amount_usd_minor
        invoice["payments"]["paid_amount"] = _money(paid)
        invoice["due_amount"] = _money(invoice["total_minor"] - paid)
        invoice["status"] = "PAID" if paid >= invoice["total_minor"] else "PARTIALLY_PAID"
        self.store.put("invoice", invoice_id, invoice)
        result = {"payment_id": payment_id}
        self.store.put("invoice_payment_request", request_id, result)
        return result

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        self._maybe_fail("get_invoice")
        return self._invoice(invoice_id)

    def cancel_invoice(self, invoice_id: str, *, note: str, request_id: str) -> dict[str, Any]:
        self._maybe_fail("cancel_invoice")
        invoice = self._invoice(invoice_id)
        if invoice["status"] == "CANCELLED":
            return {}
        if invoice["status"] in ("PAID", "PARTIALLY_PAID"):
            raise self._wrong_status(invoice)
        invoice["status"] = "CANCELLED"
        self.store.put("invoice", invoice_id, invoice)
        return {}

    # ------------------------------------------------------------------ webhooks

    def verify_webhook_signature(
        self, *, headers: Mapping[str, str], raw_body: bytes, webhook_id: str
    ) -> bool:
        # Like the real client, a delivery missing any of the five headers is not checked at all.
        if not webhook_id or not all(headers.get(name) for name in WEBHOOK_HEADERS):
            return False
        transmission_id = headers["PAYPAL-TRANSMISSION-ID"]
        given = headers["PAYPAL-TRANSMISSION-SIG"]
        return hmac.compare_digest(given, webhook_signature(webhook_id, transmission_id, raw_body))


def _money(minor: int, *, key: str = "currency_code") -> dict[str, str]:
    return {key: CURRENCY, "value": format_amount(minor)}
