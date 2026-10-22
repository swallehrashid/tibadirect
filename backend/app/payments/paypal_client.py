"""PayPal REST client for the collector account (orders, refunds, payouts, webhook checks).

One class talks to PayPal; ``paypal_mock.MockPayPal`` has the same methods and returns the same
JSON shapes, so services never know which one they hold. ``get_client()`` picks by ``PAYPAL_MODE``.

* Access tokens come from the client-credentials grant and are cached until 60 seconds before they
  expire (in memory and in Redis, so several workers share one token).
* Every call that changes something sends ``PayPal-Request-Id``, so a retry after a timeout cannot
  charge, refund or pay twice, and asks for ``Prefer: return=representation``.
* Timeouts are 15 seconds. A 429, a 5xx or a dropped connection is retried with a short backoff;
  a 401 fetches a fresh token once. Everything else is mapped to a ``PayPalError``.
* Only methods, paths, status codes and PayPal's ``paypal-debug-id`` are logged, never a body.
"""

import base64
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import requests
from flask import current_app
from redis.exceptions import RedisError

from app.config import ConfigError
from app.extensions import get_redis
from app.money import format_amount
from app.payments import errors

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15
TOKEN_SAFETY_SECONDS = 60
RETRY_DELAYS = (0.5, 1.0, 2.0)
RETRY_AFTER_CAP_SECONDS = 5.0
TOKEN_KEY_PREFIX = "tibadirect:paypal:token:"

CURRENCY = "USD"
MODES = ("mock", "sandbox")


@dataclass(frozen=True)
class PayoutItem:
    """One recipient of a payout batch. ``amount_usd_minor`` is US cents."""

    receiver_email: str
    amount_usd_minor: int
    sender_item_id: str
    note: str


class PayPalGateway(Protocol):
    """What the rest of the app may ask of PayPal. Results are PayPal's JSON, as documented."""

    def create_order(
        self,
        *,
        amount_usd_minor: int,
        reference_id: str,
        custom_id: str,
        description: str,
        request_id: str,
    ) -> dict[str, Any]: ...

    def get_order(self, order_id: str) -> dict[str, Any]: ...

    def capture_order(self, order_id: str, *, request_id: str) -> dict[str, Any]: ...

    def refund_capture(
        self, capture_id: str, *, amount_usd_minor: int, note: str, request_id: str
    ) -> dict[str, Any]: ...

    def create_payout(
        self,
        *,
        sender_batch_id: str,
        items: list[PayoutItem],
        email_subject: str,
        request_id: str,
    ) -> dict[str, Any]: ...

    def get_payout(self, batch_id: str) -> dict[str, Any]: ...

    def verify_webhook_signature(
        self, *, headers: Mapping[str, str], raw_body: bytes, webhook_id: str
    ) -> bool: ...


def _amount(minor: int) -> dict[str, str]:
    return {"currency_code": CURRENCY, "value": format_amount(minor)}


class PayPalClient:
    """The real client. Build it with ``get_client()`` unless you hold other credentials."""

    def __init__(
        self,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ConfigError("PayPal client ID and secret are required outside mock mode")
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._session = session or requests.Session()
        self._token: str | None = None
        self._token_expires_at = 0.0
        digest = hashlib.sha256(f"{self.base_url}|{client_id}".encode()).hexdigest()
        self._token_key = TOKEN_KEY_PREFIX + digest[:24]

    # ------------------------------------------------------------------ token

    def _cached_token(self) -> str | None:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        try:
            stored = get_redis().get(self._token_key)
            remaining = get_redis().ttl(self._token_key)
        except RedisError:
            return None
        if stored and remaining and remaining > 0:
            self._token = stored.decode()
            self._token_expires_at = time.monotonic() + remaining
            return self._token
        return None

    def _fetch_token(self) -> str:
        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        try:
            response = self._session.post(
                f"{self.base_url}/v1/oauth2/token",
                headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
                data={"grant_type": "client_credentials"},
                timeout=self._timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise errors.unavailable(type(exc).__name__) from exc
        debug_id = response.headers.get("paypal-debug-id")
        logger.info("paypal token status=%s debug_id=%s", response.status_code, debug_id)
        if response.status_code != 200:
            error = errors.from_response(response.status_code, _json_or_none(response), debug_id)
            if response.status_code in (400, 401):
                # Bad credentials surface as auth failures however PayPal words them.
                status, code, message = errors.AUTH_FAILED
                error = errors.PayPalError(
                    status_code=status,
                    code=code,
                    message=message,
                    http_status=response.status_code,
                    debug_id=debug_id,
                )
            raise error
        body = response.json()
        token = str(body["access_token"])
        lifetime = max(int(body.get("expires_in", 0)) - TOKEN_SAFETY_SECONDS, 0)
        self._token = token
        self._token_expires_at = time.monotonic() + lifetime
        if lifetime > 0:
            try:
                get_redis().set(self._token_key, token, ex=lifetime)
            except RedisError:
                logger.warning("paypal token could not be cached in redis")
        return token

    def _access_token(self, *, refresh: bool = False) -> str:
        if not refresh and (token := self._cached_token()):
            return token
        return self._fetch_token()

    def _forget_token(self) -> None:
        self._token = None
        self._token_expires_at = 0.0
        with contextlib.suppress(RedisError):
            get_redis().delete(self._token_key)

    # ------------------------------------------------------------------ requests

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: bytes | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        read_only: bool = False,
    ) -> dict[str, Any]:
        """Send a request and return PayPal's JSON body ({} for an empty one).

        Anything that changes state needs a ``request_id``; ``read_only`` marks a POST that does
        not (the webhook check).
        """
        if method != "GET" and not read_only and not request_id:
            raise ValueError("A PayPal call that changes something needs a request_id")
        refreshed = False
        attempt = 0
        while True:
            token = self._access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if request_id:
                headers["PayPal-Request-Id"] = request_id
                headers["Prefer"] = "return=representation"
            try:
                response = self._session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=json_body if data is None else None,
                    data=data,
                    params=params,
                    timeout=self._timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt < len(RETRY_DELAYS):
                    self._pause(attempt, None)
                    attempt += 1
                    continue
                raise errors.unavailable(type(exc).__name__) from exc

            debug_id = response.headers.get("paypal-debug-id")
            logger.info(
                "paypal %s %s status=%s debug_id=%s", method, path, response.status_code, debug_id
            )
            if response.status_code == 401 and not refreshed:
                refreshed = True
                self._forget_token()
                self._access_token(refresh=True)
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt < len(
                RETRY_DELAYS
            ):
                self._pause(attempt, response.headers.get("Retry-After"))
                attempt += 1
                continue
            if response.status_code >= 400:
                raise errors.from_response(response.status_code, _json_or_none(response), debug_id)
            return _json_or_none(response) or {}

    @staticmethod
    def _pause(attempt: int, retry_after: str | None) -> None:
        delay = RETRY_DELAYS[attempt]
        if retry_after and retry_after.isdigit():
            delay = min(max(delay, float(retry_after)), RETRY_AFTER_CAP_SECONDS)
        time.sleep(delay)

    # ------------------------------------------------------------------ orders

    def create_order(
        self,
        *,
        amount_usd_minor: int,
        reference_id: str,
        custom_id: str,
        description: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Create a Checkout order for one donation. Nothing medical or personal goes in it."""
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "custom_id": custom_id,
                    "description": description,
                    "amount": _amount(amount_usd_minor),
                }
            ],
        }
        return self._request("POST", "/v2/checkout/orders", json_body=body, request_id=request_id)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/checkout/orders/{order_id}")

    def capture_order(self, order_id: str, *, request_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/v2/checkout/orders/{order_id}/capture", json_body={}, request_id=request_id
        )

    # ------------------------------------------------------------------ refunds

    def refund_capture(
        self, capture_id: str, *, amount_usd_minor: int, note: str, request_id: str
    ) -> dict[str, Any]:
        body = {"amount": _amount(amount_usd_minor), "note_to_payer": note}
        return self._request(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            json_body=body,
            request_id=request_id,
        )

    # ------------------------------------------------------------------ payouts

    def create_payout(
        self,
        *,
        sender_batch_id: str,
        items: list[PayoutItem],
        email_subject: str,
        request_id: str,
    ) -> dict[str, Any]:
        body = {
            "sender_batch_header": {
                "sender_batch_id": sender_batch_id,
                "email_subject": email_subject,
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "receiver": item.receiver_email,
                    "amount": {"currency": CURRENCY, "value": format_amount(item.amount_usd_minor)},
                    "sender_item_id": item.sender_item_id,
                    "note": item.note,
                }
                for item in items
            ],
        }
        return self._request("POST", "/v1/payments/payouts", json_body=body, request_id=request_id)

    def get_payout(self, batch_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/payments/payouts/{batch_id}")

    # ------------------------------------------------------------------ webhooks

    def verify_webhook_signature(
        self, *, headers: Mapping[str, str], raw_body: bytes, webhook_id: str
    ) -> bool:
        """Ask PayPal whether a delivery is genuine.

        The event is spliced into the request exactly as it arrived: PayPal checks a signature
        over the original bytes, which re-serialising parsed JSON could change.
        """
        try:
            event_text = raw_body.decode().strip()
            if not isinstance(json.loads(event_text), dict):
                return False
        except (ValueError, UnicodeDecodeError):
            return False
        fields = {
            "auth_algo": headers.get("PAYPAL-AUTH-ALGO", ""),
            "cert_url": headers.get("PAYPAL-CERT-URL", ""),
            "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID", ""),
            "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG", ""),
            "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id": webhook_id,
        }
        if not all(fields.values()):
            return False
        head = json.dumps(fields, separators=(",", ":"))
        data = (head[:-1] + ',"webhook_event":' + event_text + "}").encode()
        result = self._request(
            "POST", "/v1/notifications/verify-webhook-signature", data=data, read_only=True
        )
        return result.get("verification_status") == "SUCCESS"


def _json_or_none(response: requests.Response) -> dict[str, Any] | None:
    if not response.content:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def get_client() -> PayPalGateway:
    """The client for the configured ``PAYPAL_MODE``, created once per application."""
    existing = current_app.extensions.get("paypal_gateway")
    if existing is not None:
        return existing
    config = current_app.config
    mode = config["PAYPAL_MODE"]
    if mode not in MODES:
        raise ConfigError(f"PAYPAL_MODE must be one of {', '.join(MODES)}, not {mode!r}")
    gateway: PayPalGateway
    if mode == "mock":
        from app.payments.paypal_mock import MockPayPal

        gateway = MockPayPal.from_config(config)
    else:
        gateway = PayPalClient(
            base_url=config["PAYPAL_API_BASE"],
            client_id=config["PAYPAL_CLIENT_ID"],
            client_secret=config["PAYPAL_CLIENT_SECRET"],
        )
    current_app.extensions["paypal_gateway"] = gateway
    return gateway
