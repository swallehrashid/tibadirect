"""Hospital portal and invoicing: a confirmed bill is invoiced and the portal shows the status."""

from collections.abc import Callable

import pytest

from app.models.verification import Confirmation
from app.payments.paypal_mock import MockPayPal
from tests.api.test_hospital_portal import _decide
from tests.browser import Browser
from tests.factories import FxRateFactory
from tests.hospital_helpers import Ward, make_ward

BrowserFor = Callable[..., Browser]


@pytest.fixture
def ward(db_session) -> Ward:  # type: ignore[no-untyped-def]
    return make_ward()


@pytest.fixture
def credentials(app, monkeypatch, ward):  # type: ignore[no-untyped-def]
    code = ward.hospital.register_entry.register_code
    monkeypatch.setitem(
        app.config, "HOSPITAL_PAYPAL_CREDENTIALS", {code: {"client_id": "i", "client_secret": "s"}}
    )


def test_confirming_a_bill_issues_the_invoice_and_the_answer_shows_it(  # type: ignore[no-untyped-def]
    browser_for: BrowserFor,
    ward,
    credentials,
    paypal: MockPayPal,
    db_session,
) -> None:
    FxRateFactory()
    db_session.commit()

    response = _decide(browser_for(ward.clerk.user), ward)

    assert response.status_code == 200
    confirmation = response.get_json()["confirmation"]
    assert confirmation["paypal_invoice_id"].startswith("INV2-")
    assert confirmation["invoice_status"] == "SENT"
    invoice = paypal.get_invoice(confirmation["paypal_invoice_id"])
    assert invoice["items"][0]["name"] == "Medical bill INV-2026-0412"
    assert invoice["status"] == "SENT"


def test_the_case_detail_keeps_showing_the_invoice_status(  # type: ignore[no-untyped-def]
    browser_for: BrowserFor,
    ward,
    credentials,
    paypal: MockPayPal,
    db_session,
) -> None:
    FxRateFactory()
    db_session.commit()
    browser = browser_for(ward.clerk.user)
    _decide(browser, ward)
    confirmation = db_session.query(Confirmation).one()
    paypal.cancel_invoice(confirmation.paypal_invoice_id, note="x", request_id="x")
    from app.services import invoicing_service

    invoicing_service.refresh_status(db_session, confirmation.id)

    detail = browser.get(f"/api/hospital/cases/{ward.case.id}")

    assert detail.status_code == 200
    shown = detail.get_json()["confirmations"][0]
    assert shown["paypal_invoice_id"] == confirmation.paypal_invoice_id
    assert shown["invoice_status"] == "CANCELLED"


def test_a_hospital_without_credentials_confirms_as_before(  # type: ignore[no-untyped-def]
    browser_for: BrowserFor,
    ward,
    paypal: MockPayPal,
) -> None:
    response = _decide(browser_for(ward.clerk.user), ward)

    assert response.status_code == 200
    confirmation = response.get_json()["confirmation"]
    assert confirmation["paypal_invoice_id"] is None
    assert confirmation["invoice_status"] is None


def test_a_paypal_outage_never_stops_the_confirmation(  # type: ignore[no-untyped-def]
    browser_for: BrowserFor,
    ward,
    credentials,
    paypal: MockPayPal,
    db_session,
) -> None:
    FxRateFactory()
    db_session.commit()
    paypal.fail_next("create_invoice", http_status=503, name="SERVICE_UNAVAILABLE")

    response = _decide(browser_for(ward.clerk.user), ward)

    assert response.status_code == 200
    assert response.get_json()["case_status"] == "live"
    assert response.get_json()["confirmation"]["invoice_status"] is None  # the sweep will do it
