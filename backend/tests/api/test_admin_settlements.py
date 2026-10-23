"""POST /api/admin/cases/{id}/settlements/run: the admin asks for a tranche check now."""

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy import select

from app.models.enums import SettlementStatus, UserRole
from app.models.money import Settlement
from app.models.platform import AuditLog
from tests.browser import Browser
from tests.donation_helpers import donate, settleable_case
from tests.factories import SettlementFactory, UserFactory

BrowserFor = Callable[..., Browser]


@pytest.fixture(autouse=True)
def _manual(manual_settlement: list) -> None:
    """The route creates the tranche; sending it is the worker's job, not part of the request."""


@pytest.fixture
def admin(browser_for: BrowserFor, db_session) -> Browser:  # type: ignore[no-untyped-def]
    return browser_for(UserFactory(role=UserRole.ADMIN, email="settle.admin@example.test"))


def test_the_admin_creates_a_due_tranche(admin: Browser, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case()
    donate(db_session, case, "200.00")

    response = admin.post(f"/api/admin/cases/{case.id}/settlements/run")

    assert response.status_code == 200
    body = response.get_json()
    assert body["reason"] == "created"
    assert (body["tranche_no"], body["amount_usd_minor"], body["amount_kes_minor"]) == (
        1,
        19_390,
        2_501_310,
    )
    assert body["status"] == "pending"
    settlement = db_session.execute(select(Settlement)).scalar_one()
    assert str(settlement.id) == body["settlement_id"]


def test_when_nothing_is_due_the_answer_says_why(admin: Browser, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case()
    donate(db_session, case, "10.00")

    body = admin.post(f"/api/admin/cases/{case.id}/settlements/run").get_json()

    assert body == {
        "reason": "below_threshold",
        "settlement_id": None,
        "tranche_no": None,
        "amount_usd_minor": None,
        "amount_kes_minor": None,
        "status": None,
    }


def test_the_request_is_audited(admin: Browser, db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = settleable_case()

    admin.post(f"/api/admin/cases/{case.id}/settlements/run")

    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "settlement.check_requested")
    ).scalar_one()
    assert (entry.target_type, entry.target_id) == ("case", case.id)
    assert entry.actor_user_id is not None


def test_an_unknown_case_is_a_404(admin: Browser) -> None:
    response = admin.post(f"/api/admin/cases/{uuid.uuid4()}/settlements/run")

    assert response.status_code == 404


def test_the_hospital_sees_its_tranche_with_the_providers_reference(  # type: ignore[no-untyped-def]
    browser_for: BrowserFor, db_session, paypal
) -> None:
    from app.services import settlement_service
    from tests.factories import HospitalStaffFactory

    case = settleable_case()
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement_service.execute_settlement(db_session, settlement.id)
    settlement_service.poll_settlement(db_session, settlement.id)
    staff = HospitalStaffFactory(hospital=case.hospital).user
    other = SettlementFactory()  # another hospital's tranche: never shown
    db_session.commit()

    rows = browser_for(staff).get("/api/hospital/settlements").get_json()["items"]

    assert [r["id"] for r in rows] == [str(settlement.id)]
    assert rows[0]["status"] == SettlementStatus.SUCCEEDED.value
    assert rows[0]["amount_kes"] == "25013.10"
    assert rows[0]["external_reference"] == settlement.external_batch_id
    assert str(other.id) not in repr(rows)
