"""Public case pages: who may be seen, and nothing but the whitelist."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.extensions import limiter
from app.models.enums import CaseStatus, CloseReason, ConsentScope, TrustLevel
from tests.factories import (
    CaseDocumentFactory,
    CaseFactory,
    ConfirmationFactory,
    ConsentFactory,
    DonationFactory,
    FacilityRegisterEntryFactory,
    HospitalFactory,
    ReviewItemFactory,
    TriageResultFactory,
)

PUBLIC_KEYS = {
    "slug",
    "alias",
    "region",
    "hospital_name",
    "trust_level",
    "status",
    "story",
    "photo_url",
    "is_estimate",
    "target_kes_minor",
    "target_kes",
    "pledged_kes_minor",
    "pledged_kes",
    "settled_kes_minor",
    "settled_kes",
    "usd_goal_minor",
    "usd_goal",
    "donor_count",
    "created_at",
    "live_at",
}


def _case(status: CaseStatus, *, consent: bool = True, **fields):  # type: ignore[no-untyped-def]
    case = CaseFactory(
        status=status,
        close_reason=CloseReason.FULLY_SETTLED if status == CaseStatus.CLOSED else None,
        freeze_reason="under review" if status == CaseStatus.FROZEN else None,
        **fields,
    )
    if consent:
        ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    return case


def _slugs(response) -> list[str]:  # type: ignore[no-untyped-def]
    return [item["slug"] for item in response.get_json()["items"]]


# --- The visibility rule -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "in_list", "has_page"),
    [
        (CaseStatus.DRAFT, False, False),
        (CaseStatus.PENDING_TRIAGE, False, False),
        (CaseStatus.IN_REVIEW, False, False),
        (CaseStatus.PENDING_CONFIRMATION, False, False),
        (CaseStatus.LIVE, True, True),
        (CaseStatus.FUNDED, True, True),
        (CaseStatus.CLOSED, False, True),
        (CaseStatus.CANCELLED, False, False),
        (CaseStatus.FROZEN, False, False),
    ],
)
def test_a_case_is_public_only_in_the_right_status_with_the_consent(
    client, db_session, status: CaseStatus, in_list: bool, has_page: bool
) -> None:  # type: ignore[no-untyped-def]
    with_consent = _case(status)
    without = _case(status, consent=False)

    listed = _slugs(client.get("/api/public/cases?page_size=100"))
    assert (with_consent.public_slug in listed) is in_list
    assert without.public_slug not in listed
    assert client.get(f"/api/public/cases/{with_consent.public_slug}").status_code == (
        200 if has_page else 404
    )
    assert client.get(f"/api/public/cases/{without.public_slug}").status_code == 404


def test_withdrawing_the_public_page_consent_hides_it_at_once(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case(CaseStatus.LIVE)
    assert client.get(f"/api/public/cases/{case.public_slug}").status_code == 200
    case.active_consent(ConsentScope.PUBLIC_PAGE).withdrawn_at = datetime.now(UTC)  # type: ignore[union-attr]
    db_session.flush()
    db_session.expire(case)
    assert client.get(f"/api/public/cases/{case.public_slug}").status_code == 404
    assert case.public_slug not in _slugs(client.get("/api/public/cases"))


def test_other_consents_do_not_make_a_page_public(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case(CaseStatus.LIVE, consent=False)
    for scope in (ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.RECEIPT_SHARING):
        ConsentFactory(case=case, scope=scope)
    assert client.get(f"/api/public/cases/{case.public_slug}").status_code == 404


def test_a_hidden_case_looks_exactly_like_one_that_never_existed(client, db_session) -> None:  # type: ignore[no-untyped-def]
    hidden = _case(CaseStatus.DRAFT)
    for slug in (hidden.public_slug, "doesnotexist"):
        response = client.get(f"/api/public/cases/{slug}")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "case_not_found"
        assert response.get_json()["error"]["message"] == "Case not found."


def test_the_slug_is_not_case_sensitive(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case(CaseStatus.LIVE)
    assert client.get(f"/api/public/cases/{case.public_slug.upper()}").status_code == 200


# --- The list ----------------------------------------------------------------------------------


def test_the_list_is_open_to_anyone_and_newest_live_first(client, db_session) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    old = _case(CaseStatus.LIVE, live_at=now - timedelta(days=5))
    new = _case(CaseStatus.LIVE, live_at=now - timedelta(days=1))
    mid = _case(CaseStatus.FUNDED, live_at=now - timedelta(days=3))
    response = client.get("/api/public/cases?page_size=100")
    assert response.status_code == 200
    slugs = _slugs(response)
    assert (
        slugs.index(new.public_slug) < slugs.index(mid.public_slug) < slugs.index(old.public_slug)
    )
    assert response.get_json()["total"] == 3


def test_the_county_filter_uses_the_hospitals_county(client, db_session) -> None:  # type: ignore[no-untyped-def]
    kisumu = HospitalFactory(register_entry=FacilityRegisterEntryFactory(county="Kisumu"))
    nyeri = HospitalFactory(register_entry=FacilityRegisterEntryFactory(county="Nyeri"))
    in_kisumu = _case(CaseStatus.LIVE, hospital=kisumu, region="Nairobi")
    in_nyeri = _case(CaseStatus.LIVE, hospital=nyeri)
    assert _slugs(client.get("/api/public/cases?county=kisumu")) == [in_kisumu.public_slug]
    assert _slugs(client.get("/api/public/cases?county=%20NYERI%20")) == [in_nyeri.public_slug]
    assert _slugs(client.get("/api/public/cases?county=Mombasa")) == []
    assert len(_slugs(client.get("/api/public/cases?county="))) == 2


def test_the_list_is_paginated(client, db_session) -> None:  # type: ignore[no-untyped-def]
    for _ in range(3):
        _case(CaseStatus.LIVE)
    first = client.get("/api/public/cases?page_size=2").get_json()
    second = client.get("/api/public/cases?page_size=2&page=2").get_json()
    assert (first["total"], len(first["items"]), len(second["items"])) == (3, 2, 1)


def test_an_empty_list_is_still_a_page(client, db_session) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/public/cases").get_json() == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
    }


# --- The whitelist -----------------------------------------------------------------------------


def _loaded_case():  # type: ignore[no-untyped-def]
    """A live case with every kind of private data attached to it."""
    case = _case(
        CaseStatus.LIVE,
        alias="Baby Zawadi",
        story="We are grateful.",
        trust_level=TrustLevel.HOSPITAL_CONFIRMED,
        target_kes_minor=8_500_000,
        usd_goal_minor=69_186,
        pledged_kes_minor=2_000_000,
        settled_kes_minor=1_000_000,
        donor_count=4,
        live_at=datetime.now(UTC),
        is_estimate=False,
    )
    case.set_patient_ref("SECRET-PATIENT-REF-991")
    case.created_by.email = "private.parent@example.test"
    case.created_by.display_name = "Private Parent Name"
    case.hospital.register_entry.official_email = "billing@private-hospital.test"
    document = CaseDocumentFactory(
        case=case, redacted_text="Total KES 85,000 for SECRET-DIAGNOSIS-TEXT"
    )
    TriageResultFactory(case=case, document=document, signals=[{"name": "SECRET-SIGNAL"}])
    ConfirmationFactory(case=case, invoice_number="SECRET-INVOICE-77")
    DonationFactory(case=case, status="captured", net_usd_minor=2500)
    ReviewItemFactory(case=case, reason="SECRET-REVIEW-REASON")
    return case


def test_the_public_view_has_exactly_the_whitelisted_fields(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _loaded_case()
    detail = client.get(f"/api/public/cases/{case.public_slug}").get_json()
    listed = client.get("/api/public/cases").get_json()["items"][0]
    assert set(detail) == PUBLIC_KEYS
    assert set(listed) == PUBLIC_KEYS
    assert detail == listed


def test_nothing_private_appears_anywhere_in_the_public_json(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _loaded_case()
    text = client.get(f"/api/public/cases/{case.public_slug}").get_data(as_text=True) + client.get(
        "/api/public/cases"
    ).get_data(as_text=True)
    for secret in (
        "SECRET-PATIENT-REF-991",
        "private.parent",
        "Private Parent",
        "private-hospital.test",
        "SECRET-DIAGNOSIS-TEXT",
        "SECRET-SIGNAL",
        "SECRET-INVOICE-77",
        "SECRET-REVIEW-REASON",
        str(case.id),
        str(case.created_by_id),
        "patient_ref",
        "created_by",
        "documents",
        "triage",
    ):
        assert secret not in text, secret


def test_the_public_values_are_correct_and_formatted(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _loaded_case()
    body = client.get(f"/api/public/cases/{case.public_slug}").get_json()
    assert body["slug"] == case.public_slug
    assert body["hospital_name"] == case.hospital.display_name
    assert body["trust_level"] == "hospital_confirmed"
    assert body["status"] == "live"
    assert body["target_kes"] == "85000.00"
    assert body["pledged_kes"] == "20000.00"
    assert body["settled_kes"] == "10000.00"
    assert body["usd_goal"] == "691.86"
    assert body["donor_count"] == 4
    assert body["photo_url"] is None
    assert body["is_estimate"] is False


def test_a_case_with_no_target_yet_shows_nulls_not_errors(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case(CaseStatus.LIVE, target_kes_minor=None, usd_goal_minor=None)
    body = client.get(f"/api/public/cases/{case.public_slug}").get_json()
    assert body["target_kes"] is None
    assert body["usd_goal"] is None


def test_no_sign_in_or_csrf_token_is_needed(app, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case(CaseStatus.LIVE)
    anonymous = app.test_client()
    assert anonymous.get(f"/api/public/cases/{case.public_slug}").status_code == 200
    assert "Set-Cookie" not in anonymous.get(f"/api/public/cases/{case.public_slug}").headers


def test_the_whole_response_is_valid_json_with_no_surprises(client, db_session) -> None:  # type: ignore[no-untyped-def]
    _loaded_case()
    json.loads(client.get("/api/public/cases").get_data(as_text=True))


# --- Scraping ----------------------------------------------------------------------------------


def test_one_address_cannot_read_the_list_without_limit(client, db_session) -> None:  # type: ignore[no-untyped-def]
    limiter.reset()
    statuses = [client.get("/api/public/cases").status_code for _ in range(121)]
    assert statuses[:120] == [200] * 120
    assert statuses[120] == 429
    limiter.reset()


def test_the_routes_are_documented(client) -> None:  # type: ignore[no-untyped-def]
    spec = client.get("/api/openapi.json").get_json()
    assert "/api/public/cases" in spec["paths"]
    assert "/api/public/cases/{slug}" in spec["paths"]
    assert "404" in spec["paths"]["/api/public/cases/{slug}"]["get"]["responses"]
    assert set(spec["components"]["schemas"]["PublicCase"]["properties"]) == PUBLIC_KEYS
