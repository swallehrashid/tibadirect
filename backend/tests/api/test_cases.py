"""A family's own cases over HTTP: create, read, edit, delete and who may touch what."""

from collections.abc import Callable

import pytest
from sqlalchemy import select

from app.models.enums import CaseStatus, ConsentScope, UserRole
from app.models.platform import AuditLog
from tests.browser import Browser
from tests.factories import (
    CaseFactory,
    ConfirmationFactory,
    ConsentFactory,
    HospitalFactory,
    TriageResultFactory,
    UserFactory,
)

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


def _new_case_body(hospital, **overrides) -> dict:  # type: ignore[no-untyped-def]
    return {
        "hospital_id": str(hospital.id),
        "alias": "Baby Zawadi",
        "region": "Nairobi",
        "patient_reference": "ADM-2026/0412",
        "is_minor": False,
        **overrides,
    }


# --- Create ------------------------------------------------------------------------------------


def test_a_family_member_creates_a_draft(browser_for: BrowserFor, db_session) -> None:  # type: ignore[no-untyped-def]
    user, hospital = UserFactory(), HospitalFactory()
    response = browser_for(user).post("/api/cases", _new_case_body(hospital))
    assert response.status_code == 201
    case = response.get_json()
    assert case["status"] == "draft"
    assert case["trust_level"] == "unverified"
    assert case["alias"] == "Baby Zawadi"
    assert case["patient_reference"] == "ADM-2026/0412"
    assert case["hospital"] == {
        "id": str(hospital.id),
        "display_name": hospital.display_name,
        "county": hospital.county,
    }
    assert case["is_publicly_visible"] is False
    assert case["consents"] == []
    assert case["timeline"][0]["status"] == "draft"
    assert case["triage"] is None
    assert case["confirmations"] == []
    assert case["money"]["pledged_kes"] == "0.00"
    assert case["is_accepting_donations"] is False


@pytest.mark.parametrize(
    ("overrides", "field", "code"),
    [
        ({"alias": "x"}, "alias", "alias_length"),
        ({"alias": "Fighting cancer"}, "alias", "alias_medical_terms"),
        ({"region": "12"}, "region", "region_invalid"),
        ({"patient_reference": "x"}, "patient_reference", "patient_reference_invalid"),
        (
            {"hospital_id": "00000000-0000-0000-0000-000000000000"},
            "hospital_id",
            "hospital_not_found",
        ),
    ],
)
def test_bad_input_is_a_422_that_names_the_field(
    browser_for: BrowserFor, overrides: dict, field: str, code: str
) -> None:
    hospital = HospitalFactory()
    response = browser_for(UserFactory()).post("/api/cases", _new_case_body(hospital, **overrides))
    assert response.status_code == 422
    assert _error(response)["code"] == code
    assert field in _error(response)["details"]["json"]


@pytest.mark.parametrize("missing", ["hospital_id", "alias", "region", "patient_reference"])
def test_missing_fields_are_422(browser_for: BrowserFor, missing: str) -> None:
    body = _new_case_body(HospitalFactory())
    del body[missing]
    response = browser_for(UserFactory()).post("/api/cases", body)
    assert response.status_code == 422
    assert _error(response)["code"] == "validation_error"
    assert missing in _error(response)["details"]["json"]


def test_a_malformed_hospital_id_is_422(browser_for: BrowserFor) -> None:
    body = _new_case_body(HospitalFactory(), hospital_id="not-a-uuid")
    assert browser_for(UserFactory()).post("/api/cases", body).status_code == 422


def test_creating_is_audited(browser_for: BrowserFor, db_session) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory()
    response = browser_for(user).post("/api/cases", _new_case_body(HospitalFactory()))
    row = db_session.execute(select(AuditLog).where(AuditLog.action == "case.created")).scalar_one()
    assert str(row.target_id) == response.get_json()["id"]
    assert row.request_id == response.headers["X-Request-ID"]


def test_the_eleventh_case_in_an_hour_is_limited(browser_for: BrowserFor) -> None:
    browser = browser_for(UserFactory())
    hospital = HospitalFactory()
    statuses = [
        browser.post(
            "/api/cases", _new_case_body(hospital, alias=f"Child {chr(65 + i)}{chr(66 + i)}")
        ).status_code
        for i in range(11)
    ]
    assert statuses == [201] * 10 + [429]


def test_the_limit_is_per_user_not_per_address(browser_for: BrowserFor) -> None:
    hospital = HospitalFactory()
    first = browser_for(UserFactory())
    second = browser_for(UserFactory())
    for i in range(10):
        first.post(
            "/api/cases", _new_case_body(hospital, alias=f"Child {chr(65 + i)}{chr(66 + i)}")
        )
    assert second.post("/api/cases", _new_case_body(hospital)).status_code == 201


# --- Roles -------------------------------------------------------------------------------------


def test_anonymous_callers_get_401(browser: Browser) -> None:
    case = CaseFactory()
    for response in (
        browser.get("/api/cases"),
        browser.post("/api/cases", _new_case_body(HospitalFactory())),
        browser.get(f"/api/cases/{case.id}"),
        browser.patch(f"/api/cases/{case.id}", {"alias": "Other Name"}),
        browser.delete(f"/api/cases/{case.id}"),
    ):
        assert response.status_code == 401


def test_staff_and_admins_use_their_own_routes_not_these(browser_for: BrowserFor) -> None:
    from tests.factories import HospitalStaffFactory

    case = CaseFactory()
    staff = HospitalStaffFactory()
    for user in (staff.user, UserFactory(role=UserRole.ADMIN)):
        browser = browser_for(user)
        assert browser.get("/api/cases").status_code == 403
        assert browser.get(f"/api/cases/{case.id}").status_code == 403


# --- Read --------------------------------------------------------------------------------------


def test_my_cases_lists_only_mine_newest_first_and_without_the_patient_reference(
    browser_for: BrowserFor,
) -> None:
    user = UserFactory()
    older = CaseFactory(created_by=user, alias="Older Case")
    newer = CaseFactory(created_by=user, alias="Newer Case")
    CaseFactory(alias="Someone Else")
    body = browser_for(user).get("/api/cases").get_json()
    assert body["total"] == 2
    assert {c["alias"] for c in body["items"]} == {"Older Case", "Newer Case"}
    assert "patient_reference" not in body["items"][0]
    assert "story" not in body["items"][0]
    assert {older.id.hex, newer.id.hex} == {c["id"].replace("-", "") for c in body["items"]}


def test_the_list_is_paginated(browser_for: BrowserFor) -> None:
    user = UserFactory()
    for index in range(3):
        CaseFactory(created_by=user, alias=f"Case number {index}")
    browser = browser_for(user)
    first = browser.get("/api/cases?page_size=2").get_json()
    second = browser.get("/api/cases?page_size=2&page=2").get_json()
    assert (first["total"], len(first["items"]), len(second["items"])) == (3, 2, 1)


def test_the_detail_shows_money_checks_confirmations_and_consents(browser_for: BrowserFor) -> None:
    user = UserFactory()
    case = CaseFactory(
        created_by=user,
        status=CaseStatus.LIVE,
        target_kes_minor=8_500_000,
        usd_goal_minor=69_186,
        pledged_kes_minor=2_000_000,
        settled_kes_minor=1_000_000,
        captured_net_usd_minor=15_503,
        donor_count=4,
    )
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    TriageResultFactory(case=case, document__case=case, reasons_en=["Looks consistent"])
    ConfirmationFactory(case=case, amount_kes_minor=8_500_000, is_final_bill=False)

    body = browser_for(user).get(f"/api/cases/{case.id}").get_json()

    assert body["consents"] == ["hospital_verification", "public_page"]
    assert body["is_publicly_visible"] is True
    assert body["money"]["target_kes_minor"] == 8_500_000
    assert body["money"]["target_kes"] == "85000.00"
    assert body["money"]["pledged_kes"] == "20000.00"
    assert body["money"]["settled_kes"] == "10000.00"
    assert body["money"]["usd_goal"] == "691.86"
    assert body["money"]["donor_count"] == 4
    assert body["triage"]["reasons_en"] == ["Looks consistent"]
    assert body["triage"]["risk_band"] == "low"
    [confirmation] = body["confirmations"]
    assert confirmation["status"] == "confirmed"
    assert confirmation["amount_kes_minor"] == 8_500_000
    assert "confirmed_by_staff_id" not in confirmation
    assert body["is_accepting_donations"] is True


def test_the_family_view_does_not_expose_internals(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    text = browser_for(case.created_by).get(f"/api/cases/{case.id}").get_data(as_text=True)
    for forbidden in ("patient_ref_enc", "patient_ref_hmac", "version_id", "created_by", "email"):
        assert forbidden not in text


def test_the_timeline_follows_the_case(browser_for: BrowserFor, db_session) -> None:  # type: ignore[no-untyped-def]
    from app.services import case_service

    case = CaseFactory()
    case_service.transition(case, CaseStatus.PENDING_TRIAGE, case.created_by)
    body = browser_for(case.created_by).get(f"/api/cases/{case.id}").get_json()
    assert [entry["status"] for entry in body["timeline"]] == ["draft", "pending_triage"]


# --- Edit and delete ---------------------------------------------------------------------------


def test_a_draft_can_be_edited(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    browser = browser_for(case.created_by)
    response = browser.patch(
        f"/api/cases/{case.id}",
        {
            "alias": "Baby Zuri",
            "story": "We are grateful for any help.",
            "patient_reference": "IP-9",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert (body["alias"], body["story"], body["patient_reference"]) == (
        "Baby Zuri",
        "We are grateful for any help.",
        "IP-9",
    )


def test_a_story_with_medical_words_is_refused_and_the_words_are_named(
    browser_for: BrowserFor,
) -> None:
    case = CaseFactory()
    response = browser_for(case.created_by).patch(
        f"/api/cases/{case.id}", {"story": "He has malaria and needs help"}
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "story_medical_terms"
    assert _error(response)["details"]["terms"] == ["malaria"]


@pytest.mark.parametrize(
    "body",
    [
        {"status": "live"},
        {"hospital_id": "00000000-0000-0000-0000-000000000000"},
        {"target_kes_minor": 1},
        {"pledged_kes_minor": 1_000_000},
        {"trust_level": "hospital_confirmed"},
        {"public_slug": "abcdefghijkl"},
        {"created_by_id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_fields_a_family_must_not_control_are_rejected(browser_for: BrowserFor, body: dict) -> None:
    case = CaseFactory()
    response = browser_for(case.created_by).patch(f"/api/cases/{case.id}", body)
    assert response.status_code == 422


def test_a_case_past_draft_cannot_be_edited_or_deleted(browser_for: BrowserFor) -> None:
    case = CaseFactory(status=CaseStatus.PENDING_TRIAGE)
    browser = browser_for(case.created_by)
    edit = browser.patch(f"/api/cases/{case.id}", {"alias": "Changed Name"})
    delete = browser.delete(f"/api/cases/{case.id}")
    assert (edit.status_code, _error(edit)["code"]) == (409, "case_not_editable")
    assert (delete.status_code, _error(delete)["code"]) == (409, "case_not_draft")


def test_deleting_a_draft_cancels_it_and_it_stays_visible_to_its_owner(
    browser_for: BrowserFor,
) -> None:
    case = CaseFactory()
    browser = browser_for(case.created_by)
    assert browser.delete(f"/api/cases/{case.id}").status_code == 204
    assert browser.get(f"/api/cases/{case.id}").get_json()["status"] == "cancelled"


def test_marking_a_case_as_a_childs_needs_guardian_consent_over_http(
    browser_for: BrowserFor,
) -> None:
    case = CaseFactory()
    ConsentFactory(case=case, scope=ConsentScope.RECEIPT_SHARING)
    response = browser_for(case.created_by).patch(f"/api/cases/{case.id}", {"is_minor": True})
    assert (response.status_code, _error(response)["code"]) == (409, "guardian_consent_required")


def test_concurrent_edits_are_a_409_not_a_lost_update(
    browser_for: BrowserFor, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.orm.exc import StaleDataError

    from app.services import case_service

    case = CaseFactory()

    def clash(*args: object, **kwargs: object) -> None:
        raise StaleDataError("version mismatch")

    monkeypatch.setattr(case_service, "update_draft", clash)
    response = browser_for(case.created_by).patch(f"/api/cases/{case.id}", {"alias": "Other Name"})
    assert response.status_code == 409
    assert _error(response)["code"] == "conflict"


# --- IDOR --------------------------------------------------------------------------------------


def test_someone_elses_case_looks_exactly_like_a_missing_one(browser_for: BrowserFor) -> None:
    import uuid

    case = CaseFactory(alias="Private Case")
    stranger = browser_for(UserFactory())
    for method, body in (("get", None), ("patch", {"alias": "Hijacked"}), ("delete", None)):
        theirs = getattr(stranger, method)(
            f"/api/cases/{case.id}", **({"json": body} if body else {})
        )
        missing = getattr(stranger, method)(
            f"/api/cases/{uuid.uuid4()}", **({"json": body} if body else {})
        )
        assert theirs.status_code == missing.status_code == 404
        assert _error(theirs)["code"] == _error(missing)["code"] == "case_not_found"
        assert _error(theirs)["message"] == _error(missing)["message"]
    assert case.alias == "Private Case"
    assert case.status == CaseStatus.DRAFT


def test_the_routes_are_documented(client) -> None:  # type: ignore[no-untyped-def]
    paths = client.get("/api/openapi.json").get_json()["paths"]
    assert set(paths["/api/cases"]) == {"get", "post"}
    assert set(paths["/api/cases/{case_id}"]) - {"parameters"} == {"get", "patch", "delete"}
    assert "FamilyCase" in client.get("/api/openapi.json").get_json()["components"]["schemas"]
