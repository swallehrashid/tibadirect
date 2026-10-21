"""The hospital portal over HTTP: codes to the register address, decisions, two approvals."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.case import ReviewItem
from app.models.enums import (
    CaseStatus,
    ConfirmationRequestStatus,
    ConfirmationStatus,
    ConsentScope,
    ReviewSource,
    UserRole,
)
from app.models.platform import AuditLog
from app.models.verification import Confirmation, ConfirmationRequest
from app.services import mail_service
from tests.browser import Browser
from tests.factories import (
    CaseFactory,
    ConfirmationFactory,
    ConsentFactory,
    HospitalStaffFactory,
    SettlementFactory,
    UserFactory,
)
from tests.hospital_helpers import (
    CLERK_EMAIL,
    REGISTER_EMAIL,
    Ward,
    last_code_sent_to,
    make_ward,
    masked,
)

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


def _send(browser: Browser, ward: Ward, purpose: str = "decision"):  # type: ignore[no-untyped-def]
    return browser.post(f"/api/hospital/cases/{ward.case.id}/otp", {"purpose": purpose})


def _code(browser: Browser, ward: Ward, purpose: str = "decision") -> tuple[str, str]:
    """Ask for a code and read it from the register mailbox: returns (request_id, code)."""
    response = _send(browser, ward, purpose)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["request_id"], last_code_sent_to(REGISTER_EMAIL)


def _decision_body(request_id: str, code: str, **overrides) -> dict:  # type: ignore[no-untyped-def]
    return {
        "request_id": request_id,
        "otp": code,
        "decision": "confirmed",
        "amount_kes": "85000.00",
        "invoice_number": "INV-2026-0412",
        "is_final_bill": False,
        **overrides,
    }


def _decide(browser: Browser, ward: Ward, **overrides):  # type: ignore[no-untyped-def]
    request_id, code = _code(browser, ward)
    return browser.post(
        f"/api/hospital/cases/{ward.case.id}/decision",
        _decision_body(request_id, code, **overrides),
    )


@pytest.fixture
def ward(db_session) -> Ward:  # type: ignore[no-untyped-def]
    return make_ward()


# --- Who may use it ----------------------------------------------------------------------------


def test_anonymous_family_and_admin_callers_are_refused(
    browser_for: BrowserFor, browser: Browser, ward: Ward
) -> None:
    assert browser.get("/api/hospital/queue").status_code == 401
    for user in (UserFactory(), UserFactory(role=UserRole.ADMIN)):
        assert browser_for(user).get("/api/hospital/queue").status_code == 403


def test_staff_who_are_switched_off_are_refused(browser_for: BrowserFor, ward: Ward) -> None:
    ward.clerk.is_active = False
    assert browser_for(ward.clerk.user).get("/api/hospital/queue").status_code == 403


# --- Queue and case detail ---------------------------------------------------------------------


def test_the_queue_shows_this_hospitals_waiting_bills_only(
    browser_for: BrowserFor, ward: Ward
) -> None:
    other_hospital_case = make_ward(other=True).case  # a different hospital's waiting case
    draft = CaseFactory(hospital=ward.hospital, status=CaseStatus.DRAFT)
    live = CaseFactory(hospital=ward.hospital, status=CaseStatus.LIVE)
    body = browser_for(ward.clerk.user).get("/api/hospital/queue").get_json()
    ids = {item["id"] for item in body["pending"]}
    assert ids == {str(ward.case.id)}
    assert str(other_hospital_case.id) not in ids
    assert not ids & {str(draft.id), str(live.id)}
    assert body["awaiting_second_approval"] == []
    [item] = body["pending"]
    assert set(item) == {"id", "alias", "region", "submitted_at", "risk_band", "bills"}
    assert item["bills"] == 1


def test_a_bill_waiting_for_a_second_approval_moves_to_its_own_list(
    browser_for: BrowserFor, db_session
) -> None:  # type: ignore[no-untyped-def]
    ward = make_ward(threshold_kes=50_000)
    clerk = browser_for(ward.clerk.user)
    assert _decide(clerk, ward).status_code == 200
    body = browser_for(ward.supervisor.user).get("/api/hospital/queue").get_json()
    assert body["pending"] == []
    [waiting] = body["awaiting_second_approval"]
    assert waiting["status"] == "awaiting_second_approval"
    assert waiting["confirmed_by"] == "Clerk One"
    assert waiting["amount_kes_minor"] == 8_500_000


def test_case_detail_shows_what_the_hospital_needs_and_no_family_identity(
    browser_for: BrowserFor, ward: Ward
) -> None:
    ward.case.set_patient_ref("ADM-2026/0412")
    body = browser_for(ward.clerk.user).get(f"/api/hospital/cases/{ward.case.id}").get_json()
    assert body["patient_reference"] == "ADM-2026/0412"
    assert body["status"] == "pending_confirmation"
    assert body["otp_destination_masked"] == masked(REGISTER_EMAIL)
    assert body["two_person_threshold_kes_minor"] == 20_000_000
    assert [bill["kind"] for bill in body["bills"]] == ["invoice"]
    assert body["triage"] is None
    assert body["confirmations"] == []
    text = str(body)
    for private in ("mama@example.test", "created_by", "email"):
        assert private not in text


def test_case_detail_is_hidden_from_other_hospitals_and_early_cases(
    browser_for: BrowserFor, ward: Ward
) -> None:
    outsider = HospitalStaffFactory()
    assert browser_for(outsider.user).get(f"/api/hospital/cases/{ward.case.id}").status_code == 404
    draft = CaseFactory(hospital=ward.hospital, status=CaseStatus.DRAFT)
    response = browser_for(ward.clerk.user).get(f"/api/hospital/cases/{draft.id}")
    assert (response.status_code, _error(response)["code"]) == (404, "case_not_found")


# --- One-time codes ----------------------------------------------------------------------------


def test_the_code_goes_only_to_the_registers_official_address(
    browser_for: BrowserFor, ward: Ward
) -> None:
    response = _send(browser_for(ward.clerk.user), ward)
    assert response.status_code == 201
    body = response.get_json()
    assert body["destination_masked"] == masked(REGISTER_EMAIL)
    assert set(body) == {"request_id", "destination_masked", "expires_at"}
    [message] = mail_service.outbox
    assert message.to == REGISTER_EMAIL
    assert CLERK_EMAIL not in repr(mail_service.outbox)
    assert "mama@example.test" not in repr(mail_service.outbox)
    code = last_code_sent_to(REGISTER_EMAIL)
    assert code not in response.get_data(as_text=True)
    assert "Clerk One" in message.text
    assert "Baby Zawadi" in message.text


def test_the_register_address_wins_even_if_the_staff_member_has_another(
    browser_for: BrowserFor, ward: Ward
) -> None:
    ward.hospital.paypal_payout_email = "payouts@elsewhere.test"
    _send(browser_for(ward.clerk.user), ward)
    assert {m.to for m in mail_service.outbox} == {REGISTER_EMAIL}


def test_only_a_keyed_hash_of_the_code_is_stored(
    browser_for: BrowserFor, ward: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    request_id, code = _code(browser_for(ward.clerk.user), ward)
    stored = db_session.get(ConfirmationRequest, request_id)
    assert len(stored.otp_hmac) == 64
    assert code not in stored.otp_hmac
    assert stored.destination_masked == masked(REGISTER_EMAIL)
    assert timedelta(minutes=9) < stored.expires_at - datetime.now(UTC) <= timedelta(minutes=10)
    assert stored.status == ConfirmationRequestStatus.SENT


def test_a_new_code_cancels_the_staff_members_earlier_one(
    browser_for: BrowserFor, ward: Ward
) -> None:
    clerk = browser_for(ward.clerk.user)
    first_id, first_code = _code(clerk, ward)
    second_id, second_code = _code(clerk, ward)
    stale = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(first_id, first_code)
    )
    assert _error(stale)["code"] == "otp_expired"
    assert first_id != second_id


def test_sending_a_code_is_limited_to_five_an_hour_per_case_whoever_asks(
    browser_for: BrowserFor, ward: Ward
) -> None:
    clerk, supervisor = (
        browser_for(ward.clerk.user),
        browser_for(ward.supervisor.user, "198.51.100.77"),
    )
    statuses = [_send(clerk if i % 2 else supervisor, ward).status_code for i in range(6)]
    assert statuses == [201] * 5 + [429]


def test_a_failed_email_is_a_503_and_leaves_no_usable_code(
    browser_for: BrowserFor,
    ward: Ward,
    monkeypatch: pytest.MonkeyPatch,
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    def down(*args: object, **kwargs: object) -> None:
        raise mail_service.MailError("ConnectionRefusedError")

    monkeypatch.setattr(mail_service, "send", down)
    response = _send(browser_for(ward.clerk.user), ward)
    assert (response.status_code, _error(response)["code"]) == (503, "otp_not_sent")
    db_session.expire_all()
    assert not db_session.execute(
        select(ConfirmationRequest).where(
            ConfirmationRequest.status == ConfirmationRequestStatus.SENT
        )
    ).first()


def test_an_unknown_purpose_is_a_validation_error(browser_for: BrowserFor, ward: Ward) -> None:
    response = _send(browser_for(ward.clerk.user), ward, "approve_everything")
    assert response.status_code == 422


def test_a_second_approval_code_needs_a_pending_second_approval(
    browser_for: BrowserFor, ward: Ward
) -> None:
    response = _send(browser_for(ward.supervisor.user), ward, "second_approval")
    assert (response.status_code, _error(response)["code"]) == (409, "no_second_approval_pending")


def test_a_decision_code_needs_a_case_waiting_for_a_decision(
    browser_for: BrowserFor, ward: Ward
) -> None:
    ward.case.status = CaseStatus.LIVE
    response = _send(browser_for(ward.clerk.user), ward)
    assert (response.status_code, _error(response)["code"]) == (
        409,
        "case_not_awaiting_confirmation",
    )


def test_codes_are_audited_without_the_code_or_the_address(
    browser_for: BrowserFor, ward: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    request_id, code = _code(browser_for(ward.clerk.user), ward)
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "confirmation.otp_sent")
    ).scalar_one()
    assert entry.details == {"purpose": "decision", "confirmation_request_id": request_id}
    assert code not in repr(entry.details)
    assert "kilele" not in repr(entry.details)


# --- Confirming --------------------------------------------------------------------------------


def test_a_confirmation_below_the_threshold_takes_the_case_live(
    browser_for: BrowserFor, ward: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    response = _decide(browser_for(ward.clerk.user), ward, deposit_kes="10000.00")
    assert response.status_code == 200
    body = response.get_json()
    assert body["case_status"] == "live"
    confirmation = body["confirmation"]
    assert confirmation["status"] == "confirmed"
    assert confirmation["amount_kes_minor"] == 8_500_000
    assert confirmation["amount_kes"] == "85000.00"
    assert confirmation["invoice_number"] == "INV-2026-0412"
    assert confirmation["deposit_kes_minor"] == 1_000_000
    assert confirmation["confirmed_by"] == "Clerk One"
    assert confirmation["second_approver"] is None
    assert confirmation["finalized_at"] is not None

    case = ward.case
    db_session.refresh(case)
    assert case.status == CaseStatus.LIVE
    assert case.trust_level.value == "hospital_confirmed"
    assert case.target_kes_minor == 8_500_000
    assert case.is_estimate is True
    assert case.live_at is not None
    assert case.usd_goal_minor == 69_187  # 85,000 x 1.05 / 129 = 691.86, rounded up
    assert case.is_publicly_visible


def test_a_final_bill_is_not_an_estimate(browser_for: BrowserFor, ward: Ward, db_session) -> None:  # type: ignore[no-untyped-def]
    _decide(browser_for(ward.clerk.user), ward, is_final_bill=True)
    db_session.refresh(ward.case)
    assert ward.case.is_estimate is False


def test_amounts_are_converted_to_minor_units_exactly(browser_for: BrowserFor, ward: Ward) -> None:
    body = _decide(browser_for(ward.clerk.user), ward, amount_kes="85000.50").get_json()
    assert body["confirmation"]["amount_kes_minor"] == 8_500_050


def test_the_family_hears_that_the_page_is_live(browser_for: BrowserFor, ward: Ward) -> None:
    _decide(browser_for(ward.clerk.user), ward)
    live = [m for m in mail_service.outbox if m.template == "case_live"]
    assert [m.to for m in live] == ["mama@example.test"]
    assert f"/cases/{ward.case.public_slug}" in live[0].text
    assert "Baby Zawadi" in live[0].text


def test_the_decision_is_audited_with_a_band_not_an_amount(
    browser_for: BrowserFor, ward: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    _decide(browser_for(ward.clerk.user), ward)
    actions = {a.action: a for a in db_session.execute(select(AuditLog)).scalars()}
    assert actions["confirmation.recorded"].details["amount_band"] == "below_threshold"
    assert actions["confirmation.confirmed"].details["two_person"] is False
    assert "8500000" not in repr([a.details for a in actions.values()])
    assert actions["case.transitioned"].details == {
        "from": "pending_confirmation",
        "to": "live",
        "reason": "hospital_confirmed",
    }


def test_without_the_public_page_consent_the_bill_is_confirmed_but_the_case_waits(
    browser_for: BrowserFor, db_session
) -> None:  # type: ignore[no-untyped-def]
    ward = make_ward(public_consent=False)
    body = _decide(browser_for(ward.clerk.user), ward).get_json()
    assert body["confirmation"]["status"] == "confirmed"
    assert body["case_status"] == "pending_confirmation"
    db_session.refresh(ward.case)
    assert ward.case.target_kes_minor == 8_500_000
    assert not [m for m in mail_service.outbox if m.template == "case_live"]


def test_granting_the_public_page_consent_later_takes_a_confirmed_case_live(
    browser_for: BrowserFor, db_session
) -> None:  # type: ignore[no-untyped-def]
    ward = make_ward(public_consent=False)
    _decide(browser_for(ward.clerk.user), ward)
    family = browser_for(ward.case.created_by)
    response = family.post(
        f"/api/cases/{ward.case.id}/consents",
        {"scope": "public_page", "actor": "self", "wording_version": "v1", "language": "en"},
    )
    assert response.status_code == 201
    db_session.refresh(ward.case)
    assert ward.case.status == CaseStatus.LIVE
    assert [m.template for m in mail_service.outbox].count("case_live") == 1


def test_the_family_sees_the_confirmation_in_their_view(
    browser_for: BrowserFor, ward: Ward
) -> None:
    _decide(browser_for(ward.clerk.user), ward)
    body = browser_for(ward.case.created_by).get(f"/api/cases/{ward.case.id}").get_json()
    [confirmation] = body["confirmations"]
    assert confirmation["status"] == "confirmed"
    assert confirmation["amount_kes_minor"] == 8_500_000
    assert body["is_accepting_donations"] is True


# --- The two-person rule -----------------------------------------------------------------------


@pytest.fixture
def big_bill(db_session) -> Ward:  # type: ignore[no-untyped-def]
    return make_ward(threshold_kes=50_000)


def test_a_bill_at_or_above_the_threshold_waits_for_a_second_person(
    browser_for: BrowserFor, big_bill: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    body = _decide(browser_for(big_bill.clerk.user), big_bill, amount_kes="50000.00").get_json()
    assert body["confirmation"]["status"] == "awaiting_second_approval"
    assert body["confirmation"]["finalized_at"] is None
    assert body["case_status"] == "pending_confirmation"
    db_session.refresh(big_bill.case)
    assert big_bill.case.target_kes_minor is None
    assert big_bill.case.status == CaseStatus.PENDING_CONFIRMATION
    assert not [m for m in mail_service.outbox if m.template == "case_live"]


def test_a_bill_just_below_the_threshold_needs_only_one_person(
    browser_for: BrowserFor, big_bill: Ward
) -> None:
    body = _decide(browser_for(big_bill.clerk.user), big_bill, amount_kes="49999.99").get_json()
    assert body["case_status"] == "live"


def _first_confirmation(browser_for: BrowserFor, ward: Ward) -> str:
    return _decide(browser_for(ward.clerk.user), ward).get_json()["confirmation"]["id"]


def test_a_different_supervisor_approves_with_a_fresh_code_and_the_case_goes_live(
    browser_for: BrowserFor, big_bill: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    confirmation_id = _first_confirmation(browser_for, big_bill)
    supervisor = browser_for(big_bill.supervisor.user)
    request_id, code = _code(supervisor, big_bill, "second_approval")
    response = supervisor.post(
        f"/api/hospital/confirmations/{confirmation_id}/approve",
        {"request_id": request_id, "otp": code},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["case_status"] == "live"
    assert body["confirmation"]["status"] == "confirmed"
    assert body["confirmation"]["confirmed_by"] == "Clerk One"
    assert body["confirmation"]["second_approver"] == "Supervisor Two"
    db_session.refresh(big_bill.case)
    assert big_bill.case.target_kes_minor == 8_500_000
    confirmation = db_session.get(Confirmation, confirmation_id)
    assert confirmation.second_approver_staff_id == big_bill.supervisor.id
    assert confirmation.second_approver_staff_id != confirmation.confirmed_by_staff_id
    assert str(confirmation.second_request_id) == request_id
    assert confirmation.confirmation_request_id != confirmation.second_request_id


def test_the_same_person_cannot_give_both_approvals(
    browser_for: BrowserFor, big_bill: Ward
) -> None:
    supervisor = browser_for(big_bill.supervisor.user)
    confirmation_id = _decide(supervisor, big_bill).get_json()["confirmation"]["id"]
    asked = _send(supervisor, big_bill, "second_approval")
    assert (asked.status_code, _error(asked)["code"]) == (403, "same_person_cannot_approve")
    # even with a code borrowed from the first decision's request
    other = browser_for(big_bill.supervisor.user)
    approve = other.post(
        f"/api/hospital/confirmations/{confirmation_id}/approve",
        {"request_id": "00000000-0000-0000-0000-000000000000", "otp": "123456"},
    )
    assert (approve.status_code, _error(approve)["code"]) == (403, "same_person_cannot_approve")


def test_a_clerk_cannot_give_the_second_approval(browser_for: BrowserFor, big_bill: Ward) -> None:
    other_supervisor_confirms = browser_for(big_bill.supervisor.user)
    confirmation_id = _decide(other_supervisor_confirms, big_bill).get_json()["confirmation"]["id"]
    clerk = browser_for(big_bill.clerk.user)
    asked = _send(clerk, big_bill, "second_approval")
    assert (asked.status_code, _error(asked)["code"]) == (403, "supervisor_required")
    approve = clerk.post(
        f"/api/hospital/confirmations/{confirmation_id}/approve",
        {"request_id": "00000000-0000-0000-0000-000000000000", "otp": "123456"},
    )
    assert (approve.status_code, _error(approve)["code"]) == (403, "supervisor_required")


def test_staff_of_another_hospital_cannot_approve(browser_for: BrowserFor, big_bill: Ward) -> None:
    confirmation_id = _first_confirmation(browser_for, big_bill)
    outsider = browser_for(HospitalStaffFactory(staff_role="billing_supervisor").user)
    response = outsider.post(
        f"/api/hospital/confirmations/{confirmation_id}/approve",
        {"request_id": "00000000-0000-0000-0000-000000000000", "otp": "123456"},
    )
    assert (response.status_code, _error(response)["code"]) == (404, "confirmation_not_found")


def test_a_second_approval_needs_a_second_approval_code_not_a_decision_code(
    browser_for: BrowserFor, big_bill: Ward
) -> None:
    confirmation_id = _first_confirmation(browser_for, big_bill)
    supervisor = browser_for(big_bill.supervisor.user)
    # The supervisor asks for a *decision* code on a case that has none pending: refused.
    assert _send(supervisor, big_bill, "decision").status_code == 409
    response = supervisor.post(
        f"/api/hospital/confirmations/{confirmation_id}/approve",
        {"request_id": "00000000-0000-0000-0000-000000000000", "otp": "123456"},
    )
    assert (response.status_code, _error(response)["code"]) == (422, "otp_invalid")


def test_a_bill_waiting_for_approval_cannot_be_decided_again(
    browser_for: BrowserFor, big_bill: Ward
) -> None:
    _first_confirmation(browser_for, big_bill)
    response = _send(browser_for(big_bill.clerk.user), big_bill)
    assert (response.status_code, _error(response)["code"]) == (409, "awaiting_second_approval")


def test_a_confirmed_confirmation_cannot_be_approved_twice(
    browser_for: BrowserFor, big_bill: Ward
) -> None:
    confirmation_id = _first_confirmation(browser_for, big_bill)
    supervisor = browser_for(big_bill.supervisor.user)
    request_id, code = _code(supervisor, big_bill, "second_approval")
    body = {"request_id": request_id, "otp": code}
    assert (
        supervisor.post(f"/api/hospital/confirmations/{confirmation_id}/approve", body).status_code
        == 200
    )
    again = supervisor.post(f"/api/hospital/confirmations/{confirmation_id}/approve", body)
    assert again.status_code == 409


# --- Code checks -------------------------------------------------------------------------------


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def test_a_wrong_code_is_refused_and_the_right_one_still_works(
    browser_for: BrowserFor, ward: Ward
) -> None:
    clerk = browser_for(ward.clerk.user)
    request_id, code = _code(clerk, ward)
    bad = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, _wrong(code))
    )
    assert (bad.status_code, _error(bad)["code"]) == (422, "otp_invalid")
    assert ward.case.status == CaseStatus.PENDING_CONFIRMATION
    good = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
    )
    assert good.status_code == 200


def test_five_wrong_codes_lock_the_request_even_for_the_right_one(
    browser_for: BrowserFor, ward: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    clerk = browser_for(ward.clerk.user)
    request_id, code = _code(clerk, ward)
    codes = []
    for _ in range(5):
        response = clerk.post(
            f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, _wrong(code))
        )
        codes.append(_error(response)["code"])
    assert codes == ["otp_invalid"] * 4 + ["otp_locked"]
    late = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
    )
    assert _error(late)["code"] == "otp_locked"
    stored = db_session.get(ConfirmationRequest, request_id)
    assert (stored.attempts, stored.status) == (5, ConfirmationRequestStatus.LOCKED)
    assert ward.case.status == CaseStatus.PENDING_CONFIRMATION


def test_an_expired_code_is_refused(browser_for: BrowserFor, ward: Ward, db_session) -> None:  # type: ignore[no-untyped-def]
    clerk = browser_for(ward.clerk.user)
    request_id, code = _code(clerk, ward)
    db_session.get(ConfirmationRequest, request_id).expires_at = datetime.now(UTC) - timedelta(
        seconds=1
    )
    db_session.flush()
    response = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
    )
    assert (response.status_code, _error(response)["code"]) == (422, "otp_expired")


def test_a_code_works_once(browser_for: BrowserFor, ward: Ward) -> None:
    clerk = browser_for(ward.clerk.user)
    request_id, code = _code(clerk, ward)
    first = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision",
        _decision_body(request_id, code, decision="queried"),
    )
    assert first.status_code == 200
    again = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
    )
    assert again.status_code == 409  # the case already left the queue


def test_a_code_for_another_case_or_person_or_purpose_is_invalid(
    browser_for: BrowserFor, ward: Ward
) -> None:
    other = make_ward(other=True)
    mine, theirs = browser_for(ward.clerk.user), browser_for(other.clerk.user)
    request_id, code = _code(mine, ward)
    other_request, other_code = _code(theirs, other)

    on_another_case = theirs.post(
        f"/api/hospital/cases/{other.case.id}/decision", _decision_body(request_id, code)
    )
    from_another_person = browser_for(ward.supervisor.user).post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
    )
    unknown = mine.post(
        f"/api/hospital/cases/{ward.case.id}/decision",
        _decision_body("00000000-0000-0000-0000-000000000000", code),
    )
    for response in (on_another_case, from_another_person, unknown):
        assert (response.status_code, _error(response)["code"]) == (422, "otp_invalid")
    assert other_request != request_id
    assert other_code


def test_the_code_is_bound_to_its_own_request(browser_for: BrowserFor, ward: Ward) -> None:
    clerk = browser_for(ward.clerk.user)
    first_id, first_code = _code(clerk, ward)
    # A second request from another staff member has a different code that must not open the first.
    supervisor = browser_for(ward.supervisor.user)
    second_id, second_code = _code(supervisor, ward)
    cross = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(first_id, second_code)
    )
    assert _error(cross)["code"] in {"otp_invalid", "otp_locked"}
    assert second_id != first_id
    assert first_code


@pytest.mark.parametrize("otp", ["", "12345", "1234567", "abcdef", "12 3456"])
def test_a_malformed_code_is_a_validation_error(
    browser_for: BrowserFor, ward: Ward, otp: str
) -> None:
    response = browser_for(ward.clerk.user).post(
        f"/api/hospital/cases/{ward.case.id}/decision",
        _decision_body("00000000-0000-0000-0000-000000000000", otp),
    )
    assert response.status_code == 422
    assert "otp" in _error(response)["details"]["json"]


# --- Bad amounts leave the code unused ---------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"amount_kes": None}, "amount_kes"),
        ({"amount_kes": "0.00"}, "amount_kes"),
        ({"amount_kes": "-5.00"}, "amount_kes"),
        ({"amount_kes": "50000001.00"}, "amount_kes"),
        ({"invoice_number": None}, "invoice_number"),
        ({"invoice_number": "   "}, "invoice_number"),
        ({"invoice_number": "X" * 61}, "invoice_number"),
        ({"deposit_kes": "90000.00"}, "deposit_kes"),
        ({"deposit_kes": "-1.00"}, "deposit_kes"),
    ],
)
def test_invalid_confirmation_fields_are_refused_and_the_code_stays_usable(
    browser_for: BrowserFor,
    ward: Ward,
    db_session,
    overrides: dict,
    field: str,  # type: ignore[no-untyped-def]
) -> None:
    clerk = browser_for(ward.clerk.user)
    request_id, code = _code(clerk, ward)
    response = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision",
        _decision_body(request_id, code, **overrides),
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "confirmation_invalid"
    assert field in _error(response)["details"]["json"]
    db_session.expire_all()
    assert db_session.get(ConfirmationRequest, request_id).status == ConfirmationRequestStatus.SENT
    assert ward.case.status == CaseStatus.PENDING_CONFIRMATION
    assert (
        clerk.post(
            f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
        ).status_code
        == 200
    )


def test_an_invoice_number_used_on_another_case_at_this_hospital_is_refused(
    browser_for: BrowserFor, ward: Ward
) -> None:
    earlier = CaseFactory(hospital=ward.hospital, status=CaseStatus.LIVE)
    ConfirmationFactory(case=earlier, invoice_number="inv-2026-0412")
    response = _decide(browser_for(ward.clerk.user), ward)
    assert (response.status_code, _error(response)["code"]) == (409, "invoice_already_used")
    assert ward.case.status == CaseStatus.PENDING_CONFIRMATION


def test_the_same_invoice_number_at_another_hospital_is_fine(
    browser_for: BrowserFor, ward: Ward
) -> None:
    elsewhere = CaseFactory(status=CaseStatus.LIVE)
    ConfirmationFactory(case=elsewhere, invoice_number="INV-2026-0412")
    assert _decide(browser_for(ward.clerk.user), ward).status_code == 200


def test_a_rejected_or_queried_invoice_number_does_not_block_reuse(
    browser_for: BrowserFor, ward: Ward
) -> None:
    earlier = CaseFactory(hospital=ward.hospital, status=CaseStatus.CANCELLED)
    ConfirmationFactory(
        case=earlier, invoice_number="INV-2026-0412", status=ConfirmationStatus.REJECTED
    )
    assert _decide(browser_for(ward.clerk.user), ward).status_code == 200


# --- Queries and rejections --------------------------------------------------------------------


def test_a_query_sends_the_case_to_review_and_tells_the_family(
    browser_for: BrowserFor, ward: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    response = _decide(
        browser_for(ward.clerk.user),
        ward,
        decision="queried",
        amount_kes=None,
        invoice_number=None,
        note="The total looks different from our ledger.",
    )  # fmt: skip
    assert response.status_code == 200
    body = response.get_json()
    assert body["case_status"] == "in_review"
    assert body["confirmation"]["status"] == "queried"
    assert body["confirmation"]["query_note"] == "The total looks different from our ledger."
    db_session.refresh(ward.case)
    assert ward.case.status == CaseStatus.IN_REVIEW
    assert ward.case.trust_level.value == "unverified"
    assert ward.case.target_kes_minor is None
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.source == ReviewSource.ADMIN
    assert item.payload["origin"] == "hospital_query"
    assert "total looks different" in item.reason
    assert [m.template for m in mail_service.outbox if m.to == "mama@example.test"] == [
        "case_queried"
    ]
    mail = next(m for m in mail_service.outbox if m.template == "case_queried")
    assert "total looks different" not in mail.text


def test_a_rejection_cancels_the_case_and_tells_the_family(
    browser_for: BrowserFor, ward: Ward, db_session
) -> None:  # type: ignore[no-untyped-def]
    body = _decide(
        browser_for(ward.clerk.user),
        ward,
        decision="rejected",
        amount_kes=None,
        invoice_number=None,
    ).get_json()
    assert body["case_status"] == "cancelled"
    assert body["confirmation"]["status"] == "rejected"
    db_session.refresh(ward.case)
    assert ward.case.status == CaseStatus.CANCELLED
    assert [m.template for m in mail_service.outbox if m.to == "mama@example.test"] == [
        "case_rejected"
    ]


def test_the_family_sees_the_hospitals_note_in_their_view(
    browser_for: BrowserFor, ward: Ward
) -> None:
    _decide(
        browser_for(ward.clerk.user),
        ward,
        decision="queried",
        amount_kes=None,
        invoice_number=None,
        note="Please call us.",
    )
    body = browser_for(ward.case.created_by).get(f"/api/cases/{ward.case.id}").get_json()
    assert body["confirmations"][0]["query_note"] == "Please call us."


@pytest.mark.parametrize("decision", ["maybe", "awaiting_second_approval", ""])
def test_only_three_decisions_exist(browser_for: BrowserFor, ward: Ward, decision: str) -> None:
    response = browser_for(ward.clerk.user).post(
        f"/api/hospital/cases/{ward.case.id}/decision",
        _decision_body("00000000-0000-0000-0000-000000000000", "123456", decision=decision),
    )
    assert response.status_code == 422


def test_a_decision_on_a_case_that_is_not_waiting_is_a_conflict(
    browser_for: BrowserFor, ward: Ward
) -> None:
    clerk = browser_for(ward.clerk.user)
    request_id, code = _code(clerk, ward)
    ward.case.status = CaseStatus.LIVE
    response = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
    )
    assert (response.status_code, _error(response)["code"]) == (
        409,
        "case_not_awaiting_confirmation",
    )


def test_a_case_without_a_bill_cannot_be_decided(browser_for: BrowserFor, db_session) -> None:  # type: ignore[no-untyped-def]
    ward = make_ward()
    clerk = browser_for(ward.clerk.user)
    request_id, code = _code(clerk, ward)
    ward.case.documents.remove(ward.document)
    db_session.delete(ward.document)
    db_session.flush()
    response = clerk.post(
        f"/api/hospital/cases/{ward.case.id}/decision", _decision_body(request_id, code)
    )
    assert (response.status_code, _error(response)["code"]) == (409, "bill_required")


def test_other_hospitals_staff_cannot_decide_on_my_case(
    browser_for: BrowserFor, ward: Ward
) -> None:
    outsider = browser_for(HospitalStaffFactory().user)
    for response in (
        outsider.post(f"/api/hospital/cases/{ward.case.id}/otp", {"purpose": "decision"}),
        outsider.post(
            f"/api/hospital/cases/{ward.case.id}/decision",
            _decision_body("00000000-0000-0000-0000-000000000000", "123456"),
        ),
    ):
        assert (response.status_code, _error(response)["code"]) == (404, "case_not_found")
    assert not mail_service.outbox


# --- Settlements -------------------------------------------------------------------------------


def test_settlements_lists_only_this_hospitals_payments(
    browser_for: BrowserFor, ward: Ward
) -> None:
    mine = SettlementFactory(case=ward.case, tranche_no=1, external_batch_id="BATCH-1")
    SettlementFactory(case=CaseFactory(), tranche_no=1)
    body = browser_for(ward.clerk.user).get("/api/hospital/settlements").get_json()
    assert body["total"] == 1
    [row] = body["items"]
    assert row["id"] == str(mine.id)
    assert row["case_alias"] == "Baby Zawadi"
    assert (row["tranche_no"], row["status"]) == (1, "pending")
    assert row["amount_kes"] == "25800.00"
    assert row["external_reference"] == "BATCH-1"


def test_the_portal_routes_are_documented(client) -> None:  # type: ignore[no-untyped-def]
    paths = client.get("/api/openapi.json").get_json()["paths"]
    for path in (
        "/api/hospital/queue",
        "/api/hospital/cases/{case_id}",
        "/api/hospital/cases/{case_id}/otp",
        "/api/hospital/cases/{case_id}/decision",
        "/api/hospital/confirmations/{confirmation_id}/approve",
        "/api/hospital/settlements",
    ):
        assert path in paths


def test_consent_scope_names_used_here_exist() -> None:
    assert {ConsentScope.PUBLIC_PAGE.value, ConsentScope.HOSPITAL_VERIFICATION.value}
    assert ConsentFactory  # the factory the helpers rely on is importable
