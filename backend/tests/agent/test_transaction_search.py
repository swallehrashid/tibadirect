"""PayPal's transaction report: the real request, and the fake's version of it."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import responses
from flask import Flask

from app.payments.paypal_client import PayPalClient
from app.payments.paypal_mock import MockPayPal
from tests.donation_helpers import donate, settleable_case

BASE = "https://api-m.sandbox.paypal.com"
TOKEN = {"access_token": "tok-1", "token_type": "Bearer", "expires_in": 32400}


@pytest.fixture
def client(app: Flask, redis_clean: None) -> Iterator[PayPalClient]:
    with app.app_context():
        yield PayPalClient(base_url=BASE, client_id="id", client_secret="secret")


def test_the_real_report_is_asked_for_a_window_without_the_payer(client) -> None:  # type: ignore[no-untyped-def]
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mocked:
        mocked.add(responses.POST, f"{BASE}/v1/oauth2/token", json=TOKEN)
        mocked.add(
            responses.GET,
            f"{BASE}/v1/reporting/transactions",
            json={"transaction_details": [], "total_items": 0},
        )

        result = client.search_transactions(
            start=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
            end=datetime(2026, 10, 8, 12, 30, tzinfo=UTC),
        )

        assert result["total_items"] == 0
        request = [c for c in mocked.calls if "reporting" in c.request.url][0].request
        assert "start_date=2026-10-01T00%3A00%3A00%2B0000" in request.url
        assert "end_date=2026-10-08T12%3A30%3A00%2B0000" in request.url
        assert "fields=transaction_info" in request.url  # nothing about the payer is requested
        assert "page_size=100" in request.url


def test_the_fake_reports_what_it_took_refunded_and_paid_out(
    db_session, paypal: MockPayPal
) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case(target_kes_minor=10_000_000)
    donation = donate(db_session, case, "200.00")
    paypal.refund_capture(
        donation.paypal_capture_id, amount_usd_minor=500, note="t", request_id="refund-1"
    )
    window = {
        "start": datetime.now(UTC) - timedelta(hours=1),
        "end": datetime.now(UTC) + timedelta(hours=1),
    }

    found = paypal.search_transactions(**window)

    codes = [d["transaction_info"]["transaction_event_code"] for d in found["transaction_details"]]
    assert {"T0006", "T1107"} <= set(codes)
    payment = next(
        d["transaction_info"]
        for d in found["transaction_details"]
        if d["transaction_info"]["transaction_event_code"] == "T0006"
    )
    assert payment["transaction_amount"] == {"currency_code": "USD", "value": "200.00"}
    assert payment["fee_amount"]["value"] == "-6.10"
    refund = next(
        d["transaction_info"]
        for d in found["transaction_details"]
        if d["transaction_info"]["transaction_event_code"] == "T1107"
    )
    assert refund["transaction_amount"]["value"] == "-5.00"
    assert found["total_items"] == len(found["transaction_details"])


def test_the_fake_leaves_out_what_is_outside_the_window(db_session, paypal: MockPayPal) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case(target_kes_minor=10_000_000)
    donate(db_session, case, "50.00")
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)

    found = paypal.search_transactions(start=long_ago, end=long_ago + timedelta(days=3))

    assert found["transaction_details"] == []


def test_a_failure_can_be_injected_into_the_report(paypal: MockPayPal) -> None:
    from app.payments.errors import PayPalError

    paypal.fail_next("search_transactions", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError):
        paypal.search_transactions(
            start=datetime.now(UTC) - timedelta(days=1), end=datetime.now(UTC)
        )
