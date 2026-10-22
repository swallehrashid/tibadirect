"""The offline PayPal fake: same shapes as the real client, deterministic, and steerable."""

import json

import pytest
from flask import Flask

from app.payments.errors import PayPalError
from app.payments.paypal_client import PayoutItem
from app.payments.paypal_mock import (
    MemoryStore,
    MockPayPal,
    RedisStore,
    signed_webhook_headers,
    webhook_signature,
)


@pytest.fixture
def mock() -> MockPayPal:
    return MockPayPal()


def make_order(mock: MockPayPal, minor: int = 2500, request_id: str = "order-1") -> dict:
    return mock.create_order(
        amount_usd_minor=minor,
        reference_id="case-1",
        custom_id="donation-1",
        description="TibaDirect donation for case abc",
        request_id=request_id,
    )


def capture_of(result: dict) -> dict:
    return result["purchase_units"][0]["payments"]["captures"][0]


# ----------------------------------------------------------------------------- orders


def test_a_new_order_has_paypals_shape(mock: MockPayPal) -> None:
    order = make_order(mock)

    assert order["status"] == "CREATED"
    assert order["intent"] == "CAPTURE"
    assert order["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "25.00"}
    assert order["purchase_units"][0]["custom_id"] == "donation-1"
    assert any(link["rel"] == "approve" for link in order["links"])


def test_the_same_request_id_returns_the_same_order(mock: MockPayPal) -> None:
    assert make_order(mock)["id"] == make_order(mock)["id"]
    assert make_order(mock, request_id="order-2")["id"] != make_order(mock)["id"]


def test_ids_are_deterministic_across_instances() -> None:
    assert make_order(MockPayPal())["id"] == make_order(MockPayPal())["id"]


def test_capture_charges_the_default_fee_of_2_9_percent_plus_30_cents(mock: MockPayPal) -> None:
    order = make_order(mock, 2500)

    result = mock.capture_order(order["id"], request_id="cap-1")

    capture = capture_of(result)
    assert result["status"] == "COMPLETED"
    assert capture["status"] == "COMPLETED"
    breakdown = capture["seller_receivable_breakdown"]
    assert breakdown["gross_amount"] == {"currency_code": "USD", "value": "25.00"}
    assert breakdown["paypal_fee"] == {"currency_code": "USD", "value": "1.03"}  # 0.725 + 0.30
    assert breakdown["net_amount"] == {"currency_code": "USD", "value": "23.97"}
    assert result["payer"]["email_address"]
    assert capture["custom_id"] == "donation-1"


def test_the_fee_can_be_configured() -> None:
    mock = MockPayPal(fee_bps=500, fee_fixed_usd_minor=0)
    order = make_order(mock, 10000)

    capture = capture_of(mock.capture_order(order["id"], request_id="c"))

    assert capture["seller_receivable_breakdown"]["paypal_fee"]["value"] == "5.00"


def test_the_fee_never_exceeds_the_payment() -> None:
    mock = MockPayPal(fee_bps=0, fee_fixed_usd_minor=500)
    order = make_order(mock, 100)

    capture = capture_of(mock.capture_order(order["id"], request_id="c"))

    assert capture["seller_receivable_breakdown"]["net_amount"]["value"] == "0.00"


def test_repeating_a_capture_with_its_own_request_id_returns_the_same_result(
    mock: MockPayPal,
) -> None:
    order = make_order(mock)
    first = mock.capture_order(order["id"], request_id="cap-1")

    assert mock.capture_order(order["id"], request_id="cap-1") == first


def test_capturing_an_order_twice_with_different_keys_is_refused(mock: MockPayPal) -> None:
    order = make_order(mock)
    mock.capture_order(order["id"], request_id="cap-1")

    with pytest.raises(PayPalError) as caught:
        mock.capture_order(order["id"], request_id="cap-2")

    assert caught.value.code == "order_already_captured"


def test_an_order_the_donor_has_not_approved_cannot_be_captured_when_auto_approve_is_off() -> None:
    mock = MockPayPal(auto_approve=False)
    order = make_order(mock)

    with pytest.raises(PayPalError) as caught:
        mock.capture_order(order["id"], request_id="c")
    assert caught.value.code == "order_not_approved"

    mock.approve(order["id"], payer_email="gift@example.test", payer_name="Amina Otieno")
    result = mock.capture_order(order["id"], request_id="c2")
    assert result["payer"]["email_address"] == "gift@example.test"
    assert result["payer"]["name"] == {"given_name": "Amina", "surname": "Otieno"}


def test_get_order_shows_the_current_status(mock: MockPayPal) -> None:
    order = make_order(mock)
    assert mock.get_order(order["id"])["status"] == "CREATED"

    mock.capture_order(order["id"], request_id="c")

    assert mock.get_order(order["id"])["status"] == "COMPLETED"


def test_an_unknown_order_is_not_found(mock: MockPayPal) -> None:
    with pytest.raises(PayPalError) as caught:
        mock.get_order("NOPE")
    assert caught.value.code == "paypal_not_found"
    assert caught.value.status_code == 404


def test_fail_next_makes_exactly_one_call_fail_like_paypal(mock: MockPayPal) -> None:
    order = make_order(mock)
    mock.fail_next("capture_order", issue="INSTRUMENT_DECLINED")

    with pytest.raises(PayPalError) as caught:
        mock.capture_order(order["id"], request_id="c")
    assert (caught.value.status_code, caught.value.code) == (422, "payment_declined")

    assert capture_of(mock.capture_order(order["id"], request_id="c"))["status"] == "COMPLETED"


def test_fail_next_can_simulate_an_outage(mock: MockPayPal) -> None:
    mock.fail_next("create_order", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError) as caught:
        make_order(mock)

    assert caught.value.code == "paypal_unavailable"
    assert caught.value.retryable


# ----------------------------------------------------------------------------- refunds


def test_a_partial_then_final_refund_adds_up(mock: MockPayPal) -> None:
    capture_id = capture_of(mock.capture_order(make_order(mock, 5000)["id"], request_id="c"))["id"]

    first = mock.refund_capture(capture_id, amount_usd_minor=2000, note="n", request_id="r1")
    second = mock.refund_capture(capture_id, amount_usd_minor=3000, note="n", request_id="r2")

    assert first["status"] == "COMPLETED"
    assert first["amount"] == {"currency_code": "USD", "value": "20.00"}
    assert second["id"] != first["id"]
    with pytest.raises(PayPalError) as caught:
        mock.refund_capture(capture_id, amount_usd_minor=100, note="n", request_id="r3")
    assert caught.value.code == "capture_fully_refunded"


def test_a_refund_replayed_with_the_same_key_refunds_once(mock: MockPayPal) -> None:
    capture_id = capture_of(mock.capture_order(make_order(mock, 5000)["id"], request_id="c"))["id"]

    first = mock.refund_capture(capture_id, amount_usd_minor=5000, note="n", request_id="r1")
    again = mock.refund_capture(capture_id, amount_usd_minor=5000, note="n", request_id="r1")

    assert again == first


def test_a_refund_larger_than_what_is_left_is_refused(mock: MockPayPal) -> None:
    capture_id = capture_of(mock.capture_order(make_order(mock, 1000)["id"], request_id="c"))["id"]

    with pytest.raises(PayPalError) as caught:
        mock.refund_capture(capture_id, amount_usd_minor=1001, note="n", request_id="r")

    assert caught.value.code == "paypal_rejected"


def test_refunding_an_unknown_capture_is_not_found(mock: MockPayPal) -> None:
    with pytest.raises(PayPalError) as caught:
        mock.refund_capture("NOPE", amount_usd_minor=100, note="n", request_id="r")
    assert caught.value.code == "paypal_not_found"


# ----------------------------------------------------------------------------- payouts


def payout(mock: MockPayPal, sender_batch_id: str = "settle-1", request_id: str | None = None):
    return mock.create_payout(
        sender_batch_id=sender_batch_id,
        items=[PayoutItem("billing@hospital.test", 30000, "settlement-1", "tranche 1")],
        email_subject="TibaDirect settlement",
        request_id=request_id or sender_batch_id,
    )


def test_a_new_payout_is_pending_and_then_succeeds(mock: MockPayPal) -> None:
    created = payout(mock)
    batch_id = created["batch_header"]["payout_batch_id"]
    assert created["batch_header"]["batch_status"] == "PENDING"

    polled = mock.get_payout(batch_id)

    assert polled["batch_header"]["batch_status"] == "SUCCESS"
    item = polled["items"][0]
    assert item["transaction_status"] == "SUCCESS"
    assert item["payout_item"]["receiver"] == "billing@hospital.test"
    assert item["payout_item"]["amount"] == {"currency": "USD", "value": "300.00"}
    assert item["payout_item"]["sender_item_id"] == "settlement-1"
    assert item["payout_item_id"]


def test_a_payout_replayed_with_the_same_key_is_the_same_batch(mock: MockPayPal) -> None:
    assert payout(mock) == payout(mock)


def test_a_reused_batch_id_under_a_new_key_is_refused(mock: MockPayPal) -> None:
    payout(mock, "settle-1", request_id="key-a")

    with pytest.raises(PayPalError) as caught:
        payout(mock, "settle-1", request_id="key-b")

    assert caught.value.code == "paypal_rejected"


@pytest.mark.parametrize("outcome", ["FAILED", "BLOCKED", "RETURNED"])
def test_payout_items_can_end_badly(mock: MockPayPal, outcome: str) -> None:
    mock.next_payout_outcome(outcome)
    batch_id = payout(mock)["batch_header"]["payout_batch_id"]

    item = mock.get_payout(batch_id)["items"][0]

    assert item["transaction_status"] == outcome
    assert ("errors" in item) == (outcome == "FAILED")


def test_a_payout_can_stay_pending(mock: MockPayPal) -> None:
    mock.next_payout_outcome("PENDING")
    batch_id = payout(mock)["batch_header"]["payout_batch_id"]

    polled = mock.get_payout(batch_id)

    assert polled["batch_header"]["batch_status"] == "PENDING"
    assert polled["items"] == []


def test_a_payout_waits_for_its_delay() -> None:
    mock = MockPayPal(payout_delay_seconds=3600)
    batch_id = payout(mock)["batch_header"]["payout_batch_id"]

    assert mock.get_payout(batch_id)["batch_header"]["batch_status"] == "PENDING"


def test_an_unknown_payout_outcome_is_a_programming_error(mock: MockPayPal) -> None:
    with pytest.raises(ValueError, match="Unknown payout outcome"):
        mock.next_payout_outcome("MAYBE")


# ----------------------------------------------------------------------------- webhooks


def test_a_delivery_signed_for_the_fake_verifies(mock: MockPayPal) -> None:
    body = json.dumps({"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED"}).encode()
    headers = signed_webhook_headers("WEBHOOK-ID", body)

    assert mock.verify_webhook_signature(headers=headers, raw_body=body, webhook_id="WEBHOOK-ID")


def test_a_changed_body_or_another_webhook_id_does_not_verify(mock: MockPayPal) -> None:
    body = b'{"id": "WH-1"}'
    headers = signed_webhook_headers("WEBHOOK-ID", body)

    assert not mock.verify_webhook_signature(
        headers=headers, raw_body=b'{"id": "WH-2"}', webhook_id="WEBHOOK-ID"
    )
    assert not mock.verify_webhook_signature(headers=headers, raw_body=body, webhook_id="OTHER")


def test_missing_signature_headers_do_not_verify(mock: MockPayPal) -> None:
    assert not mock.verify_webhook_signature(headers={}, raw_body=b"{}", webhook_id="W")


def test_the_signature_depends_on_all_three_inputs() -> None:
    base = webhook_signature("W", "T", b"{}")
    assert base != webhook_signature("W2", "T", b"{}")
    assert base != webhook_signature("W", "T2", b"{}")
    assert base != webhook_signature("W", "T", b"{ }")


# ----------------------------------------------------------------------------- stores


def test_the_memory_store_keeps_instances_apart() -> None:
    first, second = MockPayPal(), MockPayPal()
    order = make_order(first)

    with pytest.raises(PayPalError):
        second.get_order(order["id"])


def test_the_redis_store_is_shared_between_instances(app: Flask, redis_clean: None) -> None:
    with app.app_context():
        first = MockPayPal(store=RedisStore())
        second = MockPayPal(store=RedisStore())
        order = make_order(first)

        assert second.get_order(order["id"])["id"] == order["id"]
        capture = capture_of(second.capture_order(order["id"], request_id="c"))
        assert first.get_order(order["id"])["status"] == "COMPLETED"
        assert capture["status"] == "COMPLETED"

        first.reset()
        with pytest.raises(PayPalError):
            second.get_order(order["id"])


def test_from_config_reads_the_mock_settings(app: Flask) -> None:
    config = {
        "PAYPAL_MOCK_STORE": "memory",
        "PAYPAL_MOCK_FEE_BPS": 100,
        "PAYPAL_MOCK_FEE_FIXED_USD_MINOR": 0,
        "PAYPAL_MOCK_AUTO_APPROVE": False,
    }

    mock = MockPayPal.from_config(config)

    assert isinstance(mock.store, MemoryStore)
    assert (mock.fee_bps, mock.fee_fixed_usd_minor, mock.auto_approve) == (100, 0, False)
    assert isinstance(MockPayPal.from_config({}).store, RedisStore)


def test_the_app_fixture_hands_out_a_fresh_fake_per_test(paypal: MockPayPal) -> None:
    with pytest.raises(PayPalError):
        paypal.get_order("MOCKORD-LEFT-BY-ANOTHER-TEST")
    order = make_order(paypal)
    assert paypal.get_order(order["id"])["status"] == "CREATED"
