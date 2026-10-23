"""Contract tests for the real PayPal client: requests are checked against PayPal's documented
shapes with ``responses``, so nothing here reaches the network."""

import base64
import json
import logging
from collections.abc import Iterator

import pytest
import requests
import responses
from flask import Flask

from app.config import ConfigError
from app.extensions import get_redis
from app.payments import paypal_client
from app.payments.errors import PayPalError
from app.payments.paypal_client import PayoutItem, PayPalClient, get_client
from app.payments.paypal_mock import MockPayPal

BASE = "https://api-m.sandbox.paypal.com"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"
TOKEN = {"access_token": "tok-1", "token_type": "Bearer", "expires_in": 32400}


@pytest.fixture
def client(app: Flask, redis_clean: None) -> Iterator[PayPalClient]:
    with app.app_context():
        yield PayPalClient(base_url=BASE, client_id=CLIENT_ID, client_secret=CLIENT_SECRET)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Skip the backoff pauses and record them."""
    pauses: list[float] = []
    monkeypatch.setattr(paypal_client.time, "sleep", pauses.append)
    return pauses


@pytest.fixture
def mocked() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def token_ok(mocked: responses.RequestsMock, token: str = "tok-1") -> None:
    mocked.add(responses.POST, f"{BASE}/v1/oauth2/token", json={**TOKEN, "access_token": token})


def sent(mocked: responses.RequestsMock, index: int) -> requests.PreparedRequest:
    return mocked.calls[index].request


# ----------------------------------------------------------------------------- token


def test_token_uses_client_credentials_with_basic_auth(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})

    client.get_order("ORD1")

    request = sent(mocked, 0)
    expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    assert request.body == "grant_type=client_credentials"
    assert sent(mocked, 1).headers["Authorization"] == "Bearer tok-1"


def test_token_is_cached_in_memory_and_in_redis_until_a_minute_before_expiry(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})

    client.get_order("ORD1")
    client.get_order("ORD1")

    assert [c.request.url for c in mocked.calls].count(f"{BASE}/v1/oauth2/token") == 1
    ttl = get_redis().ttl(client._token_key)
    assert 32400 - 60 - 5 <= ttl <= 32400 - 60


def test_a_second_client_reuses_the_token_stored_in_redis(
    app: Flask, client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})
    client.get_order("ORD1")

    other = PayPalClient(base_url=BASE, client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    other.get_order("ORD1")

    assert [c.request.url for c in mocked.calls].count(f"{BASE}/v1/oauth2/token") == 1


def test_an_expired_token_is_fetched_again(
    client: PayPalClient, mocked: responses.RequestsMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocked.add(
        responses.POST,
        f"{BASE}/v1/oauth2/token",
        json={**TOKEN, "expires_in": 61},
    )
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})
    client.get_order("ORD1")
    client._token_expires_at = 0.0  # in-process copy expired
    get_redis().delete(client._token_key)  # and the shared copy too

    client.get_order("ORD1")

    assert [c.request.url for c in mocked.calls].count(f"{BASE}/v1/oauth2/token") == 2


def test_a_short_lived_token_is_not_cached(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    mocked.add(responses.POST, f"{BASE}/v1/oauth2/token", json={**TOKEN, "expires_in": 30})
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})

    client.get_order("ORD1")
    client.get_order("ORD1")

    assert [c.request.url for c in mocked.calls].count(f"{BASE}/v1/oauth2/token") == 2
    assert get_redis().get(client._token_key) is None


def test_bad_credentials_are_reported_as_an_auth_failure(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    mocked.add(
        responses.POST,
        f"{BASE}/v1/oauth2/token",
        status=401,
        json={"error": "invalid_client", "error_description": "Client Authentication failed"},
        headers={"paypal-debug-id": "dbg-auth"},
    )

    with pytest.raises(PayPalError) as caught:
        client.get_order("ORD1")

    assert caught.value.code == "paypal_auth_failed"
    assert caught.value.debug_id == "dbg-auth"
    assert not caught.value.retryable


def test_a_rejected_token_is_replaced_once_and_the_call_repeated(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked, "tok-old")
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", status=401, json={})
    token_ok(mocked, "tok-new")
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})

    assert client.get_order("ORD1") == {"id": "ORD1"}

    bearer = [c.request.headers.get("Authorization") for c in mocked.calls]
    assert bearer[1] == "Bearer tok-old"
    assert bearer[-1] == "Bearer tok-new"


def test_a_second_401_is_an_auth_failure(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", status=401, json={})

    with pytest.raises(PayPalError) as caught:
        client.get_order("ORD1")

    assert caught.value.code == "paypal_auth_failed"
    assert caught.value.status_code == 502


# ----------------------------------------------------------------------------- orders


def test_create_order_sends_the_documented_body_and_idempotency_headers(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v2/checkout/orders",
        status=201,
        json={"id": "5O190127TN364715T", "status": "CREATED"},
    )

    order = client.create_order(
        amount_usd_minor=2500,
        reference_id="case-1",
        custom_id="donation-1",
        description="TibaDirect donation for case abc123",
        request_id="req-1",
    )

    assert order["id"] == "5O190127TN364715T"
    request = sent(mocked, 1)
    assert request.headers["PayPal-Request-Id"] == "req-1"
    assert request.headers["Prefer"] == "return=representation"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.body) == {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "case-1",
                "custom_id": "donation-1",
                "description": "TibaDirect donation for case abc123",
                "amount": {"currency_code": "USD", "value": "25.00"},
            }
        ],
    }


def test_capture_posts_to_the_order_and_returns_the_representation(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    body = {
        "id": "ORD1",
        "status": "COMPLETED",
        "purchase_units": [{"payments": {"captures": [{"id": "CAP1", "status": "COMPLETED"}]}}],
    }
    mocked.add(responses.POST, f"{BASE}/v2/checkout/orders/ORD1/capture", status=201, json=body)

    assert client.capture_order("ORD1", request_id="cap-req-1") == body

    request = sent(mocked, 1)
    assert request.headers["PayPal-Request-Id"] == "cap-req-1"
    assert request.headers["Prefer"] == "return=representation"


def test_reads_carry_no_idempotency_header(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})

    client.get_order("ORD1")

    assert "PayPal-Request-Id" not in sent(mocked, 1).headers


def test_a_call_that_changes_something_needs_a_request_id(client: PayPalClient) -> None:
    with pytest.raises(ValueError, match="request_id"):
        client._request("POST", "/v2/checkout/orders/ORD1/capture", json_body={})


# ----------------------------------------------------------------------------- refunds


def test_refund_posts_amount_and_note_to_the_capture(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v2/payments/captures/CAP1/refund",
        status=201,
        json={"id": "REF1", "status": "COMPLETED"},
    )

    refund = client.refund_capture(
        "CAP1", amount_usd_minor=1234, note="Your donation was refunded", request_id="ref-key"
    )

    assert refund["id"] == "REF1"
    request = sent(mocked, 1)
    assert request.headers["PayPal-Request-Id"] == "ref-key"
    assert json.loads(request.body) == {
        "amount": {"currency_code": "USD", "value": "12.34"},
        "note_to_payer": "Your donation was refunded",
    }


# ----------------------------------------------------------------------------- payouts


def test_payout_uses_the_batch_id_and_one_email_item(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v1/payments/payouts",
        status=201,
        json={"batch_header": {"payout_batch_id": "BATCH1", "batch_status": "PENDING"}},
    )

    result = client.create_payout(
        sender_batch_id="settle-abc",
        items=[
            PayoutItem(
                receiver_email="billing@hospital.test",
                amount_usd_minor=50000,
                sender_item_id="settlement-1",
                note="TibaDirect tranche 1 for invoice INV-9",
            )
        ],
        email_subject="TibaDirect settlement",
        request_id="settle-abc",
    )

    assert result["batch_header"]["payout_batch_id"] == "BATCH1"
    request = sent(mocked, 1)
    assert request.headers["PayPal-Request-Id"] == "settle-abc"
    assert json.loads(request.body) == {
        "sender_batch_header": {
            "sender_batch_id": "settle-abc",
            "email_subject": "TibaDirect settlement",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": "billing@hospital.test",
                "amount": {"currency": "USD", "value": "500.00"},
                "sender_item_id": "settlement-1",
                "note": "TibaDirect tranche 1 for invoice INV-9",
            }
        ],
    }


def test_payout_status_is_read_with_a_get(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.GET,
        f"{BASE}/v1/payments/payouts/BATCH1",
        json={"batch_header": {"batch_status": "SUCCESS"}, "items": []},
    )

    assert client.get_payout("BATCH1")["batch_header"]["batch_status"] == "SUCCESS"


# ----------------------------------------------------------------------------- retries


def test_a_server_error_is_retried_with_backoff_then_succeeds(
    client: PayPalClient, mocked: responses.RequestsMock, no_sleep: list[float]
) -> None:
    token_ok(mocked)
    mocked.add(responses.POST, f"{BASE}/v2/checkout/orders/ORD1/capture", status=503, json={})
    mocked.add(responses.POST, f"{BASE}/v2/checkout/orders/ORD1/capture", status=500, json={})
    mocked.add(
        responses.POST,
        f"{BASE}/v2/checkout/orders/ORD1/capture",
        status=201,
        json={"id": "ORD1", "status": "COMPLETED"},
    )

    assert client.capture_order("ORD1", request_id="k")["status"] == "COMPLETED"

    assert no_sleep == [0.5, 1.0]
    keys = {c.request.headers.get("PayPal-Request-Id") for c in mocked.calls[1:]}
    assert keys == {"k"}  # every retry repeats the same idempotency key


def test_server_errors_that_never_clear_become_paypal_unavailable(
    client: PayPalClient, mocked: responses.RequestsMock, no_sleep: list[float]
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.GET,
        f"{BASE}/v2/checkout/orders/ORD1",
        status=502,
        json={},
        headers={"paypal-debug-id": "dbg-502"},
    )

    with pytest.raises(PayPalError) as caught:
        client.get_order("ORD1")

    assert caught.value.code == "paypal_unavailable"
    assert caught.value.status_code == 502
    assert caught.value.retryable
    assert caught.value.debug_id == "dbg-502"
    assert len(no_sleep) == 3


def test_rate_limiting_honours_retry_after_up_to_a_cap(
    client: PayPalClient, mocked: responses.RequestsMock, no_sleep: list[float]
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.GET,
        f"{BASE}/v2/checkout/orders/ORD1",
        status=429,
        json={},
        headers={"Retry-After": "2"},
    )
    mocked.add(
        responses.GET,
        f"{BASE}/v2/checkout/orders/ORD1",
        status=429,
        json={},
        headers={"Retry-After": "600"},
    )
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", json={"id": "ORD1"})

    client.get_order("ORD1")

    assert no_sleep == [2.0, 5.0]


def test_a_timeout_is_retried_and_then_reported(
    client: PayPalClient, mocked: responses.RequestsMock, no_sleep: list[float]
) -> None:
    token_ok(mocked)
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", body=requests.Timeout())

    with pytest.raises(PayPalError) as caught:
        client.get_order("ORD1")

    assert caught.value.code == "paypal_unavailable"
    assert caught.value.retryable
    assert len(no_sleep) == 3


def test_requests_time_out_after_fifteen_seconds(
    app: Flask, redis_clean: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class Recorder(requests.Session):
        def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
            seen["timeout"] = kwargs.get("timeout")
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            return response

        def post(self, url, **kwargs):  # type: ignore[no-untyped-def]
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(TOKEN).encode()
            return response

    with app.app_context():
        PayPalClient(
            base_url=BASE, client_id=CLIENT_ID, client_secret=CLIENT_SECRET, session=Recorder()
        ).get_order("ORD1")

    assert seen["timeout"] == 15


# ----------------------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("http_status", "issue", "status", "code"),
    [
        (422, "INSTRUMENT_DECLINED", 422, "payment_declined"),
        (422, "PAYER_ACTION_REQUIRED", 422, "payer_action_required"),
        (422, "ORDER_NOT_APPROVED", 422, "order_not_approved"),
        (422, "ORDER_ALREADY_CAPTURED", 409, "order_already_captured"),
        (422, "CAPTURE_FULLY_REFUNDED", 409, "capture_fully_refunded"),
        (404, "INVALID_RESOURCE_ID", 404, "paypal_not_found"),
        (422, "SOMETHING_NEW", 502, "paypal_rejected"),
    ],
)
def test_paypal_issues_map_to_our_error_codes(
    client: PayPalClient,
    mocked: responses.RequestsMock,
    http_status: int,
    issue: str,
    status: int,
    code: str,
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v2/checkout/orders/ORD1/capture",
        status=http_status,
        json={
            "name": "UNPROCESSABLE_ENTITY",
            "message": "The payer jane@example.test was declined",
            "debug_id": "dbg-1",
            "details": [{"issue": issue, "description": "text that may name the payer"}],
        },
        headers={"paypal-debug-id": "dbg-1"},
    )

    with pytest.raises(PayPalError) as caught:
        client.capture_order("ORD1", request_id="k")

    error = caught.value
    assert (error.status_code, error.code) == (status, code)
    assert error.debug_id == "dbg-1"
    assert error.issue == issue
    assert "jane@example.test" not in error.message  # PayPal's own wording is never passed on
    assert error.details["paypal_debug_id"] == "dbg-1"


def test_insufficient_funds_on_a_payout_is_named_by_the_error_name(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v1/payments/payouts",
        status=400,
        json={"name": "INSUFFICIENT_FUNDS", "message": "Sender does not have sufficient funds"},
    )

    with pytest.raises(PayPalError) as caught:
        client.create_payout(
            sender_batch_id="b",
            items=[PayoutItem("a@b.test", 100, "s", "n")],
            email_subject="s",
            request_id="b",
        )

    assert caught.value.code == "paypal_insufficient_funds"


def test_a_non_json_error_body_still_maps_cleanly(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(responses.GET, f"{BASE}/v2/checkout/orders/ORD1", status=400, body="<html>")

    with pytest.raises(PayPalError) as caught:
        client.get_order("ORD1")

    assert caught.value.code == "paypal_rejected"


# ----------------------------------------------------------------------------- webhooks


WEBHOOK_HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api-m.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "tx-1",
    "PAYPAL-TRANSMISSION-SIG": "sig-1",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-23T09:00:00Z",
}


def test_webhook_check_sends_the_headers_and_the_event_exactly_as_received(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v1/notifications/verify-webhook-signature",
        json={"verification_status": "SUCCESS"},
    )
    raw = b'{"id": "WH-1",  "amount":1.10, "event_type":"PAYMENT.CAPTURE.COMPLETED"}'

    assert client.verify_webhook_signature(
        headers=WEBHOOK_HEADERS, raw_body=raw, webhook_id="WEBHOOK-ID"
    )

    request = sent(mocked, 1)
    text = request.body.decode()
    assert raw.decode() in text  # byte for byte, not re-serialised
    body = json.loads(text)
    assert body["auth_algo"] == "SHA256withRSA"
    assert body["cert_url"].endswith("CERT-1")
    assert body["transmission_id"] == "tx-1"
    assert body["transmission_sig"] == "sig-1"
    assert body["transmission_time"] == "2026-10-23T09:00:00Z"
    assert body["webhook_id"] == "WEBHOOK-ID"
    assert body["webhook_event"]["id"] == "WH-1"
    assert "PayPal-Request-Id" not in request.headers  # it changes nothing


def test_a_failed_verification_is_false(
    client: PayPalClient, mocked: responses.RequestsMock
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v1/notifications/verify-webhook-signature",
        json={"verification_status": "FAILURE"},
    )

    assert not client.verify_webhook_signature(
        headers=WEBHOOK_HEADERS, raw_body=b'{"id": "WH-1"}', webhook_id="W"
    )


@pytest.mark.parametrize("missing", list(WEBHOOK_HEADERS))
def test_a_delivery_missing_a_header_is_false_without_calling_paypal(
    client: PayPalClient, mocked: responses.RequestsMock, missing: str
) -> None:
    headers = {k: v for k, v in WEBHOOK_HEADERS.items() if k != missing}

    assert not client.verify_webhook_signature(
        headers=headers, raw_body=b'{"id": "WH-1"}', webhook_id="W"
    )
    assert len(mocked.calls) == 0


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"\xff\xfe"])
def test_a_delivery_that_is_not_a_json_object_is_false(
    client: PayPalClient, mocked: responses.RequestsMock, body: bytes
) -> None:
    assert not client.verify_webhook_signature(
        headers=WEBHOOK_HEADERS, raw_body=body, webhook_id="W"
    )
    assert len(mocked.calls) == 0


# ----------------------------------------------------------------------------- logging


def test_logs_name_the_call_and_debug_id_but_never_a_body(
    client: PayPalClient, mocked: responses.RequestsMock, caplog: pytest.LogCaptureFixture
) -> None:
    token_ok(mocked)
    mocked.add(
        responses.POST,
        f"{BASE}/v2/checkout/orders",
        status=201,
        json={"id": "ORD1", "payer": {"email_address": "donor@example.test"}},
        headers={"paypal-debug-id": "dbg-log"},
    )

    with caplog.at_level(logging.INFO, logger="app.payments.paypal_client"):
        client.create_order(
            amount_usd_minor=500,
            reference_id="c",
            custom_id="d",
            description="TibaDirect donation for case x",
            request_id="k",
        )

    text = caplog.text
    assert "POST /v2/checkout/orders status=201 debug_id=dbg-log" in text
    assert "donor@example.test" not in text
    assert CLIENT_SECRET not in text
    assert "tok-1" not in text


# ----------------------------------------------------------------------------- factory


def test_mock_mode_returns_the_fake_and_reuses_it(app: Flask) -> None:
    with app.app_context():
        first = get_client()
        assert isinstance(first, MockPayPal)
        assert get_client() is first


def test_sandbox_mode_needs_credentials(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setitem(app.config, "PAYPAL_CLIENT_ID", "")
    with app.app_context(), pytest.raises(ConfigError, match="client ID and secret"):
        get_client()


def test_sandbox_mode_builds_the_real_client(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setitem(app.config, "PAYPAL_CLIENT_ID", CLIENT_ID)
    monkeypatch.setitem(app.config, "PAYPAL_CLIENT_SECRET", CLIENT_SECRET)
    with app.app_context():
        gateway = get_client()
    assert isinstance(gateway, PayPalClient)
    assert gateway.base_url == BASE


def test_an_unknown_mode_is_refused(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "PAYPAL_MODE", "live")
    with app.app_context(), pytest.raises(ConfigError, match="PAYPAL_MODE"):
        get_client()
