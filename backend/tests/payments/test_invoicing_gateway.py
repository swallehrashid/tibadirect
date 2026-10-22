"""PayPal Invoicing: the real client's requests, the fake's behaviour, and who gets which client."""

import json
from collections.abc import Iterator

import pytest
import responses
from flask import Flask

from app.config import ConfigError
from app.payments.errors import PayPalError
from app.payments.paypal_client import (
    PayPalClient,
    get_client,
    get_invoicing_client,
    invoice_id_from,
)
from app.payments.paypal_mock import MockPayPal

BASE = "https://api-m.sandbox.paypal.com"
TOKEN = {"access_token": "tok-1", "token_type": "Bearer", "expires_in": 32400}
ID = "INV2-AAAA-BBBB-CCCC-DDDD"


@pytest.fixture
def client(app: Flask, redis_clean: None) -> Iterator[PayPalClient]:
    with app.app_context():
        yield PayPalClient(base_url=BASE, client_id="hospital-id", client_secret="hospital-secret")


@pytest.fixture
def mocked() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(responses.POST, f"{BASE}/v1/oauth2/token", json=TOKEN)
        yield mock


def sent(mocked: responses.RequestsMock, index: int = -1) -> tuple[str, dict, dict]:
    call = [c for c in mocked.calls if "/oauth2/" not in c.request.url][index]
    body = json.loads(call.request.body) if call.request.body else {}
    return call.request.url, body, dict(call.request.headers)


# --------------------------------------------------------------------------- the real client


def test_an_invoice_is_drafted_to_the_collector_for_one_item_payable_in_parts(
    client, mocked
) -> None:  # type: ignore[no-untyped-def]
    mocked.add(
        responses.POST,
        f"{BASE}/v2/invoicing/invoices",
        json={"rel": "self", "href": f"{BASE}/v2/invoicing/invoices/{ID}", "method": "GET"},
        status=201,
    )

    result = client.create_invoice(
        invoice_number="TD-ABC123",
        recipient_email="collector@tibadirect.test",
        item_name="Medical bill INV-2026-0412",
        amount_usd_minor=65_891,
        note="KES 85,000.00 bill confirmed by billing desk",
        invoice_date="2026-10-23",
        request_id="invoice-1",
    )

    assert invoice_id_from(result) == ID
    url, body, headers = sent(mocked)
    assert url == f"{BASE}/v2/invoicing/invoices"
    assert headers["PayPal-Request-Id"] == "invoice-1"
    assert body["detail"]["invoice_number"] == "TD-ABC123"
    assert body["detail"]["currency_code"] == "USD"
    assert body["detail"]["note"] == "KES 85,000.00 bill confirmed by billing desk"
    assert body["primary_recipients"] == [
        {"billing_info": {"email_address": "collector@tibadirect.test"}}
    ]
    assert body["items"] == [
        {
            "name": "Medical bill INV-2026-0412",
            "quantity": "1",
            "unit_amount": {"currency_code": "USD", "value": "658.91"},
        }
    ]
    assert body["configuration"]["partial_payment"] == {"allow_partial_payment": True}


def test_an_invoice_is_sent_to_the_recipient_only(client, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.add(responses.POST, f"{BASE}/v2/invoicing/invoices/{ID}/send", json={}, status=202)

    client.send_invoice(ID, request_id="invoice-send-1")

    url, body, _ = sent(mocked)
    assert url.endswith(f"/invoices/{ID}/send")
    assert body == {"send_to_recipient": True, "send_to_invoicer": False}


def test_a_tranche_is_recorded_as_a_bank_transfer_with_its_date_and_note(client, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.add(
        responses.POST,
        f"{BASE}/v2/invoicing/invoices/{ID}/payments",
        json={"payment_id": "PAY-1"},
        status=200,
    )

    result = client.record_invoice_payment(
        ID,
        amount_usd_minor=20_000,
        payment_date="2026-10-24",
        note="Tranche 1 sent to the hospital",
        request_id="invoice-payment-1",
    )

    assert result == {"payment_id": "PAY-1"}
    url, body, headers = sent(mocked)
    assert url.endswith(f"/invoices/{ID}/payments")
    assert headers["PayPal-Request-Id"] == "invoice-payment-1"
    assert body == {
        "method": "BANK_TRANSFER",
        "payment_date": "2026-10-24",
        "amount": {"currency_code": "USD", "value": "200.00"},
        "note": "Tranche 1 sent to the hospital",
    }


def test_status_is_read_and_an_invoice_can_be_cancelled(client, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.add(
        responses.GET, f"{BASE}/v2/invoicing/invoices/{ID}", json={"id": ID, "status": "SENT"}
    )
    mocked.add(responses.POST, f"{BASE}/v2/invoicing/invoices/{ID}/cancel", status=204)

    assert client.get_invoice(ID)["status"] == "SENT"
    assert client.cancel_invoice(ID, note="Replaced", request_id="invoice-cancel-1") == {}

    _, body, _ = sent(mocked)
    assert body == {"note": "Replaced", "send_to_invoicer": False, "send_to_recipient": False}


def test_paypal_refusing_an_invoice_is_an_error_with_the_issue(client, mocked) -> None:  # type: ignore[no-untyped-def]
    mocked.add(
        responses.POST,
        f"{BASE}/v2/invoicing/invoices",
        json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "DUPLICATE_INVOICE_NUMBER"}]},
        status=422,
    )

    with pytest.raises(PayPalError) as caught:
        client.create_invoice(
            invoice_number="TD-1",
            recipient_email="c@x.test",
            item_name="x",
            amount_usd_minor=100,
            note="n",
            invoice_date="2026-10-23",
            request_id="r",
        )

    assert caught.value.issue == "DUPLICATE_INVOICE_NUMBER"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"href": f"{BASE}/v2/invoicing/invoices/{ID}"}, ID),
        ({"href": f"{BASE}/v2/invoicing/invoices/{ID}/"}, ID),
        ({"id": ID}, ID),
        ({}, None),
        ({"href": ""}, None),
    ],
)
def test_the_invoice_id_is_the_last_part_of_the_link_in_the_answer(result, expected) -> None:  # type: ignore[no-untyped-def]
    assert invoice_id_from(result) == expected


# --------------------------------------------------------------------------- which client


def test_a_hospital_without_credentials_has_no_invoicing_client(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(app.config, "HOSPITAL_PAYPAL_CREDENTIALS", {"FR-OTHER-0001": {}})
    with app.app_context():
        assert get_invoicing_client("FR-MSA-0101") is None
        assert get_invoicing_client("FR-OTHER-0001") is None


def test_in_mock_mode_a_configured_hospital_shares_the_one_fake(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(
        app.config,
        "HOSPITAL_PAYPAL_CREDENTIALS",
        {"FR-MSA-0101": {"client_id": "a", "client_secret": "b"}},
    )
    with app.app_context():
        assert get_invoicing_client("FR-MSA-0101") is get_client()


def test_in_sandbox_mode_each_hospital_gets_its_own_client_kept_for_reuse(  # type: ignore[no-untyped-def]
    app: Flask, monkeypatch, redis_clean
) -> None:
    monkeypatch.setitem(app.config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setitem(
        app.config,
        "HOSPITAL_PAYPAL_CREDENTIALS",
        {
            "FR-A": {"client_id": "id-a", "client_secret": "secret-a"},
            "FR-B": {"client_id": "id-b", "client_secret": "secret-b"},
        },
    )
    with app.app_context():
        first = get_invoicing_client("FR-A")
        assert isinstance(first, PayPalClient)
        assert get_invoicing_client("FR-A") is first
        assert get_invoicing_client("FR-B") is not first
        app.extensions.pop("hospital_paypal")


def test_credentials_missing_a_secret_are_a_configuration_error(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(app.config, "PAYPAL_MODE", "sandbox")
    monkeypatch.setitem(app.config, "HOSPITAL_PAYPAL_CREDENTIALS", {"FR-A": {"client_id": "x"}})
    with app.app_context(), pytest.raises(ConfigError, match="FR-A"):
        get_invoicing_client("FR-A")
    app.extensions.pop("hospital_paypal", None)


# --------------------------------------------------------------------------- the fake


def new_invoice(fake: MockPayPal, number: str = "TD-1", usd: int = 10_000, request_id: str = "r1"):  # type: ignore[no-untyped-def]
    link = fake.create_invoice(
        invoice_number=number,
        recipient_email="collector@tibadirect.test",
        item_name="Medical bill X",
        amount_usd_minor=usd,
        note="n",
        invoice_date="2026-10-23",
        request_id=request_id,
    )
    return invoice_id_from(link)


def pay(fake: MockPayPal, invoice_id: str, usd: int, request_id: str):  # type: ignore[no-untyped-def]
    return fake.record_invoice_payment(
        invoice_id, amount_usd_minor=usd, payment_date="2026-10-24", note="t", request_id=request_id
    )


def test_the_fake_drafts_sends_and_pays_an_invoice_through_to_paid() -> None:
    fake = MockPayPal()
    invoice_id = new_invoice(fake, usd=10_000)
    assert invoice_id.startswith("INV2-")
    assert fake.get_invoice(invoice_id)["status"] == "DRAFT"

    fake.send_invoice(invoice_id, request_id="s1")
    assert fake.get_invoice(invoice_id)["status"] == "SENT"

    pay(fake, invoice_id, 4_000, "p1")
    partly = fake.get_invoice(invoice_id)
    assert partly["status"] == "PARTIALLY_PAID"
    assert partly["due_amount"]["value"] == "60.00"

    pay(fake, invoice_id, 6_000, "p2")
    assert fake.get_invoice(invoice_id)["status"] == "PAID"


def test_repeating_a_request_returns_the_same_object_and_changes_nothing() -> None:
    fake = MockPayPal()
    first = new_invoice(fake, request_id="same")
    again = new_invoice(fake, request_id="same")
    fake.send_invoice(first, request_id="s")
    one = pay(fake, first, 3_000, "same-payment")
    two = pay(fake, first, 3_000, "same-payment")

    assert first == again
    assert one == two
    assert len(fake.get_invoice(first)["payments"]["transactions"]) == 1


def test_two_invoices_cannot_share_a_number() -> None:
    fake = MockPayPal()
    new_invoice(fake, number="TD-DUP", request_id="a")

    with pytest.raises(PayPalError) as caught:
        new_invoice(fake, number="TD-DUP", request_id="b")

    assert caught.value.issue == "DUPLICATE_INVOICE_NUMBER"


def test_a_payment_needs_a_sent_invoice_and_may_not_exceed_what_is_due() -> None:
    fake = MockPayPal()
    invoice_id = new_invoice(fake, usd=5_000)

    with pytest.raises(PayPalError) as draft:
        pay(fake, invoice_id, 1_000, "p0")
    assert draft.value.issue == "INVALID_INVOICE_STATUS"

    fake.send_invoice(invoice_id, request_id="s")
    with pytest.raises(PayPalError) as over:
        pay(fake, invoice_id, 5_001, "p1")
    assert over.value.issue == "PAYMENT_AMOUNT_EXCEEDS_DUE_AMOUNT"


def test_an_unpaid_invoice_can_be_cancelled_but_a_part_paid_one_cannot() -> None:
    fake = MockPayPal()
    unpaid = new_invoice(fake, number="TD-U", request_id="u")
    fake.send_invoice(unpaid, request_id="su")
    fake.cancel_invoice(unpaid, note="x", request_id="cu")
    assert fake.get_invoice(unpaid)["status"] == "CANCELLED"
    fake.cancel_invoice(unpaid, note="x", request_id="cu")  # again: nothing happens

    part = new_invoice(fake, number="TD-P", request_id="pp")
    fake.send_invoice(part, request_id="sp")
    pay(fake, part, 1_000, "pay-p")
    with pytest.raises(PayPalError):
        fake.cancel_invoice(part, note="x", request_id="cp")


def test_an_unknown_invoice_is_not_found_and_failures_can_be_injected() -> None:
    fake = MockPayPal()
    with pytest.raises(PayPalError) as missing:
        fake.get_invoice("INV2-NOPE")
    assert missing.value.code == "paypal_not_found"

    fake.fail_next("create_invoice", http_status=503, name="SERVICE_UNAVAILABLE")
    with pytest.raises(PayPalError) as outage:
        new_invoice(fake)
    assert outage.value.retryable is True
    assert new_invoice(fake)  # the next call works
