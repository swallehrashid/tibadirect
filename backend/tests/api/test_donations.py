"""The donation endpoints as the case page uses them: order, capture, receipt."""

import json

import pytest

from app.models.enums import CaseStatus
from app.models.money import Donation
from tests.donation_helpers import calls, live_case

RECEIPT_KEYS = {
    "receipt_token",
    "status",
    "amount_usd_minor",
    "amount_usd",
    "paypal_fee_usd_minor",
    "paypal_fee_usd",
    "net_usd_minor",
    "net_usd",
    "refunded_usd_minor",
    "kes_estimate_minor",
    "kes_estimate",
    "fx_rate",
    "excess_choice",
    "donor_display_name",
    "captured_at",
    "case_slug",
    "case_alias",
    "hospital_name",
    "ledger_reference",
    "ledger_hash_short",
}


def order(client, case, **changes):  # type: ignore[no-untyped-def]
    body = {"case_slug": case.public_slug, "amount_usd": "25.00", **changes}
    return client.post("/api/donations/orders", json=body)


# --------------------------------------------------------------------------- orders


def test_an_order_is_created_for_a_live_case(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()

    response = order(client, case, excess_choice="solidarity_pool", donor_display_name="Amina")

    assert response.status_code == 201
    body = response.get_json()
    assert body["order_id"].startswith("MOCKORD")
    assert (body["amount_usd_minor"], body["amount_usd"]) == (2500, "25.00")
    donation = db_session.query(Donation).one()
    assert donation.paypal_order_id == body["order_id"]
    assert donation.excess_choice.value == "solidarity_pool"
    assert donation.is_anonymous is True  # the default is to stay anonymous


def test_defaults_are_refund_and_anonymous(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    response = order(client, live_case(), donor_display_name="Amina")

    assert response.status_code == 201
    donation = db_session.query(Donation).one()
    assert donation.excess_choice.value == "refund"
    assert donation.donor_display_name is None


def test_amounts_may_be_sent_as_numbers_too(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/api/donations/orders",
        data=f'{{"case_slug": "{live_case().public_slug}", "amount_usd": 12.5}}',
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.get_json()["amount_usd"] == "12.50"


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"amount_usd": "0.50"}, "amount_usd"),
        ({"amount_usd": "10001"}, "amount_usd"),
        ({"amount_usd": "5.555"}, "amount_usd"),
        ({"amount_usd": "abc"}, "amount_usd"),
        ({"amount_usd": "NaN"}, "amount_usd"),
        ({"excess_choice": "keep_it"}, "excess_choice"),
        ({"is_anonymous": False, "donor_display_name": "me@example.test"}, "donor_display_name"),
        ({"case_slug": ""}, "case_slug"),
    ],
)
def test_bad_input_is_a_422_naming_the_field(client, db_session, paypal, changes, field) -> None:  # type: ignore[no-untyped-def]
    response = order(client, live_case(), **changes)

    assert response.status_code == 422
    error = response.get_json()["error"]
    assert error["code"] in {"validation_error", "amount_invalid", "amount_out_of_range"} | {
        "name_personal_data"
    }
    assert field in error["details"]["json"]


def test_a_missing_amount_or_case_is_a_422(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/donations/orders", json={}).status_code == 422


def test_an_unknown_or_private_case_is_a_404(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    case.status = CaseStatus.DRAFT
    db_session.flush()

    for slug in ("nosuchcase01", case.public_slug):
        response = client.post("/api/donations/orders", json={"case_slug": slug, "amount_usd": "5"})
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "case_not_found"


def test_a_case_that_does_not_need_more_is_a_409(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    case.status = CaseStatus.FUNDED
    db_session.flush()

    response = order(client, case)

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "case_not_accepting"


def test_a_paypal_outage_is_a_502_with_our_code(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    paypal.fail_next("create_order", http_status=503, name="SERVICE_UNAVAILABLE")

    response = order(client, live_case())

    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "paypal_unavailable"
    assert db_session.query(Donation).count() == 0


def test_creating_orders_is_limited_per_address(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    statuses = [order(client, case).status_code for _ in range(21)]

    assert statuses[:20] == [201] * 20
    assert statuses[20] == 429


def test_orders_need_a_csrf_token_in_production(browser, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    body = {"case_slug": case.public_slug, "amount_usd": "5"}

    assert browser.client.post("/api/donations/orders", json=body).status_code == 403
    assert browser.post("/api/donations/orders", json=body).status_code == 201


# --------------------------------------------------------------------------- capture


def test_capture_returns_the_receipt(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    order_id = order(client, case, donor_display_name="Amina", is_anonymous=False).get_json()[
        "order_id"
    ]

    response = client.post(f"/api/donations/orders/{order_id}/capture")

    assert response.status_code == 200
    body = response.get_json()
    receipt = body["donation"]
    assert set(receipt) == RECEIPT_KEYS
    assert body["receipt_token"] == receipt["receipt_token"]
    assert (receipt["amount_usd"], receipt["paypal_fee_usd"], receipt["net_usd"]) == (
        "25.00",
        "1.03",
        "23.97",
    )
    assert receipt["kes_estimate"] == "3092.13"
    assert receipt["status"] == "captured"
    assert receipt["donor_display_name"] == "Amina"
    assert receipt["case_slug"] == case.public_slug


def test_repeating_the_capture_gives_the_same_receipt(
    client, db_session, paypal, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    order_id = order(client, live_case()).get_json()["order_id"]
    sent = calls(paypal, "capture_order", monkeypatch)

    first = client.post(f"/api/donations/orders/{order_id}/capture").get_json()
    second = client.post(f"/api/donations/orders/{order_id}/capture")

    assert second.status_code == 200
    assert second.get_json() == first
    assert len(sent) == 1


def test_capture_for_an_unknown_order_is_a_404(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    response = client.post("/api/donations/orders/NOSUCHORDER/capture")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "donation_not_found"


def test_capture_after_the_case_closed_is_a_409_and_charges_nothing(  # type: ignore[no-untyped-def]
    client, db_session, paypal, monkeypatch
) -> None:
    case = live_case()
    order_id = order(client, case).get_json()["order_id"]
    case.status = CaseStatus.CLOSED
    case.close_reason = "fully_settled"
    db_session.flush()
    sent = calls(paypal, "capture_order", monkeypatch)

    response = client.post(f"/api/donations/orders/{order_id}/capture")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "case_fully_funded"
    assert sent == []


def test_a_declined_payment_is_a_422_the_donor_can_retry(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    order_id = order(client, live_case()).get_json()["order_id"]
    paypal.fail_next("capture_order", issue="INSTRUMENT_DECLINED")

    declined = client.post(f"/api/donations/orders/{order_id}/capture")
    retried = client.post(f"/api/donations/orders/{order_id}/capture")

    assert declined.status_code == 422
    assert declined.get_json()["error"]["code"] == "payment_declined"
    assert retried.status_code == 200


def test_a_pending_payment_is_a_409_payment_pending(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    order_id = order(client, live_case()).get_json()["order_id"]
    paypal.next_capture_status("PENDING")

    response = client.post(f"/api/donations/orders/{order_id}/capture")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "payment_pending"


# --------------------------------------------------------------------------- receipts and the page


def test_the_receipt_link_shows_the_same_receipt(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    order_id = order(client, live_case()).get_json()["order_id"]
    captured = client.post(f"/api/donations/orders/{order_id}/capture").get_json()

    response = client.get(f"/api/donations/receipts/{captured['receipt_token']}")

    assert response.status_code == 200
    assert response.get_json() == captured["donation"]


def test_a_receipt_leaks_nothing_private(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    order_id = order(client, live_case()).get_json()["order_id"]
    paypal.approve(order_id, payer_email="private.donor@example.test")
    token = client.post(f"/api/donations/orders/{order_id}/capture").get_json()["receipt_token"]
    donation = db_session.query(Donation).one()

    text = json.dumps(client.get(f"/api/donations/receipts/{token}").get_json())

    for secret in (
        "private.donor",
        str(donation.id),
        donation.paypal_order_id,
        donation.paypal_capture_id,
        str(donation.case_id),
    ):
        assert secret not in text


def test_a_receipt_needs_a_real_token_for_a_taken_payment(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    order(client, live_case())
    donation = db_session.query(Donation).one()

    assert client.get(f"/api/donations/receipts/{donation.receipt_token}").status_code == 404
    assert client.get("/api/donations/receipts/not-a-token").status_code == 404


def test_the_public_page_and_ledger_show_the_donation(client, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    order_id = order(client, case, donor_display_name="Amina", is_anonymous=False).get_json()[
        "order_id"
    ]
    client.post(f"/api/donations/orders/{order_id}/capture")

    page = client.get(f"/api/public/cases/{case.public_slug}").get_json()
    ledger = client.get(f"/api/public/cases/{case.public_slug}/ledger").get_json()
    verified = client.get(f"/api/public/cases/{case.public_slug}/ledger/verify").get_json()

    assert (page["donor_count"], page["pledged_kes_minor"]) == (1, 2397 * 129)
    assert [row["donor_name"] for row in ledger["items"]] == ["Amina", None, None]
    assert [row["amount"] for row in ledger["items"]] == ["25.00", "-1.03", "1.03"]
    assert verified["valid"] is True
    assert verified["entries_checked"] == 3
