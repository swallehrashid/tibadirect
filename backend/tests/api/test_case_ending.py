"""Cancelling (family) and freezing (admin) over HTTP, with the queue running inline."""

from collections.abc import Callable

import pytest
from sqlalchemy import select

from app.models.enums import (
    CaseStatus,
    CloseReason,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    RefundReason,
    RefundStatus,
    UserRole,
)
from app.models.money import Refund
from app.services import ledger_service, mail_service
from tests.browser import Browser
from tests.donation_helpers import donate, order_for, settleable_case
from tests.factories import HospitalStaffFactory, UserFactory

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


def case_funds(session, case) -> int:  # type: ignore[no-untyped-def]
    return sum(
        e.amount_minor
        for e in session.execute(ledger_service.case_entries(case.id)).scalars()
        if e.account == LedgerAccount.CASE_FUNDS
    )


# --------------------------------------------------------------------------- family cancels


def test_the_family_cancels_a_live_case(browser_for: BrowserFor, db_session, paypal) -> None:
    case = settleable_case()
    owner = browser_for(case.created_by)

    response = owner.post(f"/api/cases/{case.id}/cancel", {"reason": "treatment_cancelled"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "cancelled"
    assert body["id"] == str(case.id)
    db_session.refresh(case)
    assert case.close_reason == CloseReason.TREATMENT_CANCELLED


@pytest.mark.parametrize("reason", ["treatment_cancelled", "patient_deceased", "other"])
def test_each_reason_is_accepted(browser_for: BrowserFor, db_session, paypal, reason: str) -> None:
    case = settleable_case()

    response = browser_for(case.created_by).post(f"/api/cases/{case.id}/cancel", {"reason": reason})

    assert response.status_code == 200


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "fraud"}, {"reason": 5}])
def test_a_missing_or_unknown_reason_is_a_422(
    browser_for: BrowserFor, db_session, paypal, body: dict
) -> None:
    case = settleable_case()

    response = browser_for(case.created_by).post(f"/api/cases/{case.id}/cancel", body)

    assert response.status_code == 422
    db_session.refresh(case)
    assert case.status == CaseStatus.LIVE


@pytest.mark.parametrize(
    "status", [CaseStatus.DRAFT, CaseStatus.CLOSED, CaseStatus.CANCELLED, CaseStatus.FROZEN]
)
def test_a_case_that_cannot_be_cancelled_is_a_409(
    browser_for: BrowserFor, db_session, paypal, status: CaseStatus
) -> None:
    case = settleable_case()
    case.status = status
    case.close_reason = CloseReason.FULLY_SETTLED if status == CaseStatus.CLOSED else None
    case.freeze_reason = "checking" if status == CaseStatus.FROZEN else None
    db_session.flush()

    response = browser_for(case.created_by).post(
        f"/api/cases/{case.id}/cancel", {"reason": "other"}
    )

    assert response.status_code == 409
    assert _error(response)["code"] == "case_not_cancellable"


def test_only_the_owner_can_cancel(
    browser_for: BrowserFor, browser: Browser, db_session, paypal
) -> None:
    case = settleable_case()
    url = f"/api/cases/{case.id}/cancel"

    assert browser_for(UserFactory()).post(url, {"reason": "other"}).status_code == 404
    assert (
        browser_for(HospitalStaffFactory(hospital=case.hospital).user)
        .post(url, {"reason": "other"})
        .status_code
        == 403
    )
    assert (
        browser_for(UserFactory(role=UserRole.ADMIN)).post(url, {"reason": "other"}).status_code
        == 403
    )
    assert browser.post(url, {"reason": "other"}).status_code == 401
    db_session.refresh(case)
    assert case.status == CaseStatus.LIVE


def test_a_cancelled_case_disappears_from_the_public_and_takes_no_money(
    browser_for: BrowserFor, browser: Browser, db_session, paypal
) -> None:
    case = settleable_case()
    browser_for(case.created_by).post(f"/api/cases/{case.id}/cancel", {"reason": "other"})

    assert browser.get(f"/api/public/cases/{case.public_slug}").status_code == 404
    orders = browser.post(
        "/api/donations/orders", {"case_slug": case.public_slug, "amount_usd": "10"}
    )
    assert orders.status_code == 404


def test_cancelling_pays_each_donor_back_or_on_as_they_chose_with_nobody_pressing_a_button(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    case = settleable_case()
    refund_me = donate(db_session, case, "25.00", excess=ExcessChoice.REFUND)
    pool_me = donate(db_session, case, "40.00", excess=ExcessChoice.SOLIDARITY_POOL)
    hold_me = donate(db_session, case, "30.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    mail_service.outbox.clear()

    response = browser_for(case.created_by).post(
        f"/api/cases/{case.id}/cancel", {"reason": "patient_deceased"}
    )

    assert response.status_code == 200
    (refund,) = db_session.execute(select(Refund)).scalars().all()
    assert (refund.donation_id, refund.reason, refund.status) == (
        refund_me.id,
        RefundReason.CANCELLATION,
        RefundStatus.SUCCEEDED,
    )
    db_session.refresh(pool_me)
    db_session.refresh(hold_me)
    assert refund_me.status == DonationStatus.REFUNDED
    assert pool_me.pooled_usd_minor == pool_me.net_usd_minor
    assert hold_me.pooled_usd_minor == hold_me.refunded_usd_minor == 0
    assert case_funds(db_session, case) == hold_me.net_usd_minor  # only the held money remains
    assert [m.template for m in mail_service.outbox] == ["refund_issued"]


def test_the_hospital_no_longer_sees_a_case_the_family_cancelled_before_it_decided(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    from tests.hospital_helpers import make_ward

    ward = make_ward()
    browser_for(ward.case.created_by).post(f"/api/cases/{ward.case.id}/cancel", {"reason": "other"})

    response = browser_for(ward.clerk.user).post(
        f"/api/hospital/cases/{ward.case.id}/otp", {"purpose": "decision"}
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------- admin freezes


def test_a_freeze_repays_every_donor_and_cancels_the_case_for_fraud(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    case = settleable_case()
    gifts = [
        donate(db_session, case, "25.00", excess=ExcessChoice.REFUND),
        donate(db_session, case, "40.00", excess=ExcessChoice.SOLIDARITY_POOL),
        donate(db_session, case, "30.00", excess=ExcessChoice.FOLLOW_UP_CARE),
    ]
    mail_service.outbox.clear()
    admin = browser_for(UserFactory(role=UserRole.ADMIN))

    response = admin.post(
        f"/api/admin/cases/{case.id}/freeze", {"reason": "Suspected forged invoice"}
    )

    assert response.status_code == 200
    db_session.refresh(case)
    # the whole thing ran through the queue: refunds made, then the freeze finished
    assert (case.status, case.close_reason) == (CaseStatus.CANCELLED, CloseReason.FRAUD)
    refunds = db_session.execute(select(Refund)).scalars().all()
    assert {r.reason for r in refunds} == {RefundReason.FREEZE}
    assert {r.status for r in refunds} == {RefundStatus.SUCCEEDED}
    for gift in gifts:
        db_session.refresh(gift)
        assert gift.status == DonationStatus.REFUNDED
        assert gift.pooled_usd_minor == 0
    assert case_funds(db_session, case) == 0
    assert ledger_service.verify_chain(db_session, case.id).valid
    sent = [m.template for m in mail_service.outbox]
    assert sent.count("refund_issued") == 3
    assert sent.count("case_frozen") == 1


def test_a_freeze_with_no_money_ends_in_a_cancellation_too(
    browser_for: BrowserFor, db_session, paypal
) -> None:
    case = settleable_case()
    order_for(db_session, case, "10.00")  # an order nobody completed
    admin = browser_for(UserFactory(role=UserRole.ADMIN))

    admin.post(f"/api/admin/cases/{case.id}/freeze", {"reason": "Suspected forged invoice"})

    db_session.refresh(case)
    assert (case.status, case.close_reason) == (CaseStatus.CANCELLED, CloseReason.FRAUD)
