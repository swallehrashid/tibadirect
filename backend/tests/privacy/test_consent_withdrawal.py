"""Each consent says what happens when it is withdrawn. These tests hold the product to those words.

The wording is in ``app/consent_texts``. Withdrawing the public page hides it but still pays the
money already given; withdrawing the hospital check cancels a case that has not been confirmed and
leaves a confirmed one alone; withdrawing the analysis abroad stops every later model call.
"""

import pytest

from app.models.enums import CaseStatus, ConsentScope, SettlementStatus
from app.services import settlement_service
from tests.browser import Browser
from tests.donation_helpers import donate, settleable_case
from tests.factories import ConsentFactory
from tests.hospital_helpers import make_ward

BIG = "200.00"


def _withdraw(browser_for, case, scope: ConsentScope):  # type: ignore[no-untyped-def]
    return browser_for(case.created_by).delete(f"/api/cases/{case.id}/consents/{scope.value}")


@pytest.fixture(autouse=True)
def _manual_settlement(manual_settlement):  # type: ignore[no-untyped-def]
    return manual_settlement


def test_withdrawing_the_public_page_hides_it_stops_gifts_and_still_pays_the_hospital(  # type: ignore[no-untyped-def]
    browser_for, browser: Browser, db_session, paypal, app, monkeypatch
) -> None:
    monkeypatch.setitem(app.config, "TRANCHE_MIN_KES", 1)
    monkeypatch.setitem(app.config, "TRANCHE_FRACTION_BPS", 1)
    case = settleable_case()  # live and public: the page consent is given
    donate(db_session, case, BIG)
    db_session.commit()
    slug = case.public_slug
    assert browser.get(f"/api/public/cases/{slug}").status_code == 200

    assert _withdraw(browser_for, case, ConsentScope.PUBLIC_PAGE).status_code == 200

    # The page and everything behind it is gone at once ...
    assert browser.get(f"/api/public/cases/{slug}").status_code == 404
    assert browser.get(f"/api/public/cases/{slug}/ledger").status_code == 404
    assert slug not in [c["slug"] for c in browser.get("/api/public/cases").get_json()["items"]]
    order = browser.post("/api/donations/orders", {"case_slug": slug, "amount_usd": "20"})
    assert order.status_code == 404
    # ... but what was already given is still paid to the hospital, as the wording promises.
    created = settlement_service.run_settlement_check(db_session, case.id)
    assert created.settlement is not None
    assert created.settlement.status == SettlementStatus.PENDING


def test_withdrawing_the_hospital_check_cancels_a_case_nobody_has_confirmed(  # type: ignore[no-untyped-def]
    browser_for, db_session
) -> None:
    ward = make_ward()
    case = ward.case
    assert _withdraw(browser_for, case, ConsentScope.HOSPITAL_VERIFICATION).status_code == 200
    db_session.expire_all()
    assert case.status == CaseStatus.CANCELLED
    # The hospital no longer sees the bill.
    clerk = browser_for(ward.clerk.user)
    assert clerk.get("/api/hospital/queue").get_json()["pending"] == []


def test_withdrawing_the_hospital_check_after_confirmation_changes_nothing(  # type: ignore[no-untyped-def]
    browser_for, db_session
) -> None:
    """The wording says "before the hospital confirms": afterwards the bill is the hospital's."""
    case = settleable_case()
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    db_session.commit()
    assert _withdraw(browser_for, case, ConsentScope.HOSPITAL_VERIFICATION).status_code == 200
    db_session.expire_all()
    assert case.status == CaseStatus.LIVE


def test_the_model_is_not_called_for_a_bill_uploaded_after_withdrawing(  # type: ignore[no-untyped-def]
    browser_for, db_session, app, monkeypatch
) -> None:
    from types import SimpleNamespace

    from app.ai import llm

    calls: list[dict] = []

    def create(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return SimpleNamespace(content=[])

    monkeypatch.setitem(app.config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setitem(app.config, "LLM_API_KEY", "test-key-not-real")
    monkeypatch.setitem(app.config, "LLM_MODEL", "test-model")
    monkeypatch.setattr(
        llm, "client_factory", lambda: SimpleNamespace(messages=SimpleNamespace(create=create))
    )

    ward = make_ward()
    case = ward.case
    ConsentFactory(case=case, scope=ConsentScope.AI_PROCESSING_ABROAD)
    db_session.commit()
    assert _withdraw(browser_for, case, ConsentScope.AI_PROCESSING_ABROAD).status_code == 200
    db_session.expire_all()

    from app.ai import pipeline

    pipeline.analyse(ward.document, raw=_one_page_bill())
    assert calls == []


def _one_page_bill() -> bytes:
    from tests.ai.helpers import text_pdf

    return text_pdf(["Kilele Mission Hospital", "INVOICE No X1", "TOTAL KES 5,000"])


def test_a_consent_can_be_given_again_after_withdrawing_it(browser_for, db_session) -> None:  # type: ignore[no-untyped-def]
    ward = make_ward()
    case = ward.case
    owner = browser_for(case.created_by)
    assert _withdraw(browser_for, case, ConsentScope.PUBLIC_PAGE).status_code == 200
    again = owner.post(
        f"/api/cases/{case.id}/consents",
        {"scope": "public_page", "actor": "self", "wording_version": "v1", "language": "en"},
    )
    assert again.status_code in (200, 201)
    history = owner.get(f"/api/cases/{case.id}/consents").get_json()
    entries = [c for c in history["items"] if c["scope"] == "public_page"]
    assert len(entries) == 2  # the withdrawn one is kept as a record, and the new one is active
    assert sorted(bool(c["withdrawn_at"]) for c in entries) == [False, True]
