"""Reading PayPal's capture JSON into the facts the services use."""

import pytest

from app.payments import parsing
from app.payments.errors import PayPalError


def capture_json(**changes: object) -> dict:
    base = {
        "id": "CAP1",
        "status": "COMPLETED",
        "amount": {"currency_code": "USD", "value": "25.00"},
        "custom_id": "donation-1",
        "seller_receivable_breakdown": {
            "gross_amount": {"currency_code": "USD", "value": "25.00"},
            "paypal_fee": {"currency_code": "USD", "value": "1.03"},
            "net_amount": {"currency_code": "USD", "value": "23.97"},
        },
    }
    return {**base, **changes}


def order_json(capture: dict | None = None, **changes: object) -> dict:
    base = {
        "id": "ORD1",
        "status": "COMPLETED",
        "payer": {"email_address": "donor@example.test"},
        "purchase_units": [{"payments": {"captures": [capture or capture_json()]}}],
    }
    return {**base, **changes}


def test_a_completed_order_gives_cents_for_gross_fee_and_net() -> None:
    details = parsing.from_order(order_json())

    assert (details.gross_usd_minor, details.fee_usd_minor, details.net_usd_minor) == (
        2500,
        103,
        2397,
    )
    assert (details.capture_id, details.order_id, details.custom_id) == (
        "CAP1",
        "ORD1",
        "donation-1",
    )
    assert details.payer_email == "donor@example.test"
    assert details.completed


def test_a_capture_without_a_breakdown_counts_in_full() -> None:
    capture = capture_json(status="PENDING")
    del capture["seller_receivable_breakdown"]

    details = parsing.from_order(order_json(capture))

    assert (details.fee_usd_minor, details.net_usd_minor) == (0, 2500)
    assert not details.completed


def test_the_webhook_resource_carries_the_order_id_in_related_ids() -> None:
    resource = capture_json(supplementary_data={"related_ids": {"order_id": "ORD9"}})

    details = parsing.from_capture_resource(resource)

    assert details.order_id == "ORD9"
    assert details.payer_email is None


def test_a_webhook_resource_without_related_ids_has_no_order_id() -> None:
    assert parsing.from_capture_resource(capture_json()).order_id is None


@pytest.mark.parametrize(
    "order",
    [
        {},
        {"purchase_units": []},
        {"purchase_units": [{"payments": {"captures": []}}]},
        {"purchase_units": [{"payments": {}}]},
        order_json(capture_json(id=None)),
        order_json(capture_json(amount=None)),
        order_json(capture_json(amount={"currency_code": "KES", "value": "25.00"})),
        order_json(capture_json(amount={"currency_code": "USD", "value": "abc"})),
        order_json(capture_json(amount={"currency_code": "USD", "value": None})),
    ],
)
def test_an_answer_that_is_not_what_paypal_documents_is_a_bad_response(order: dict) -> None:
    with pytest.raises(PayPalError) as caught:
        parsing.from_order(order)

    assert caught.value.code == "paypal_bad_response"
    assert caught.value.status_code == 502


def test_unusual_fields_are_ignored_not_trusted() -> None:
    details = parsing.from_order(
        order_json(capture_json(custom_id=123, status=None), payer={"email_address": 5})
    )

    assert details.custom_id is None
    assert details.status == "UNKNOWN"
    assert details.payer_email is None
