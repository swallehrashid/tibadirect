"""Consent over HTTP: wording, granting, withdrawing and who may do either."""

from collections.abc import Callable

import pytest

from app.models.enums import CaseStatus, UserRole
from tests.browser import Browser
from tests.factories import CaseFactory, ConsentFactory, UserFactory

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


def _body(scope: str = "public_page", **overrides) -> dict:  # type: ignore[no-untyped-def]
    return {
        "scope": scope,
        "actor": "self",
        "wording_version": "v1",
        "language": "en",
        **overrides,
    }


# --- Wording -----------------------------------------------------------------------------------


def test_the_wording_is_public_and_complete(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/consents/texts").get_json()
    assert body["language"] == "en"
    assert [item["scope"] for item in body["items"]] == [
        "hospital_verification",
        "public_page",
        "ai_processing_abroad",
        "receipt_sharing",
    ]
    for item in body["items"]:
        assert set(item) == {"scope", "language", "version", "title", "body"}
        assert item["version"] == "v1"
        assert item["language"] == "en"


def test_the_wording_comes_in_swahili(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/consents/texts?lang=sw").get_json()
    assert body["language"] == "sw"
    assert all(item["language"] == "sw" for item in body["items"])
    assert (
        body["items"][0]["title"]
        != client.get("/api/consents/texts").get_json()["items"][0]["title"]
    )


def test_an_unsupported_language_is_a_validation_error(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/api/consents/texts?lang=fr")
    assert response.status_code == 422
    assert "lang" in _error(response)["details"]["query"]


# --- Granting ----------------------------------------------------------------------------------


def test_a_family_grants_consent_to_the_current_wording(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    browser = browser_for(case.created_by)
    response = browser.post(f"/api/cases/{case.id}/consents", _body())
    assert response.status_code == 201
    consent = response.get_json()
    assert (consent["scope"], consent["actor"], consent["wording_version"]) == (
        "public_page",
        "self",
        "v1",
    )
    assert consent["active"] is True
    assert consent["withdrawn_at"] is None
    listed = browser.get(f"/api/cases/{case.id}/consents").get_json()["items"]
    assert [c["id"] for c in listed] == [consent["id"]]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (_body(scope="everything"), "scope"),
        (_body(actor="robot"), "actor"),
        (_body(language="fr"), "language"),
        (_body(wording_version=""), "wording_version"),
        ({"scope": "public_page"}, "actor"),
    ],
)
def test_a_bad_grant_is_a_validation_error(browser_for: BrowserFor, body: dict, field: str) -> None:
    case = CaseFactory()
    response = browser_for(case.created_by).post(f"/api/cases/{case.id}/consents", body)
    assert response.status_code == 422
    assert field in _error(response)["details"]["json"]


def test_an_old_wording_version_is_a_409_naming_the_current_one(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    response = browser_for(case.created_by).post(
        f"/api/cases/{case.id}/consents", _body(wording_version="v0")
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "consent_wording_outdated"
    assert _error(response)["details"] == {"current_version": "v1"}


def test_granting_twice_is_a_409(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    browser = browser_for(case.created_by)
    browser.post(f"/api/cases/{case.id}/consents", _body())
    again = browser.post(f"/api/cases/{case.id}/consents", _body())
    assert again.status_code == 409
    assert _error(again)["code"] == "consent_already_granted"


def test_a_minors_case_needs_a_guardian(browser_for: BrowserFor) -> None:
    case = CaseFactory(is_minor=True)
    browser = browser_for(case.created_by)
    refused = browser.post(f"/api/cases/{case.id}/consents", _body())
    assert refused.status_code == 409
    assert _error(refused)["code"] == "guardian_consent_required"
    accepted = browser.post(f"/api/cases/{case.id}/consents", _body(actor="guardian"))
    assert accepted.status_code == 201


def test_an_administrator_actor_is_refused_through_the_family_api(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    response = browser_for(case.created_by).post(
        f"/api/cases/{case.id}/consents", _body(actor="administrator")
    )
    assert response.status_code == 403


def test_a_closed_case_takes_no_new_consent(browser_for: BrowserFor) -> None:
    case = CaseFactory(status=CaseStatus.CANCELLED)
    response = browser_for(case.created_by).post(f"/api/cases/{case.id}/consents", _body())
    assert response.status_code == 409
    assert _error(response)["code"] == "case_not_open"


# --- Withdrawing -------------------------------------------------------------------------------


def test_withdrawing_marks_the_consent_and_keeps_it_listed(browser_for: BrowserFor) -> None:
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope="public_page")
    browser = browser_for(case.created_by)
    response = browser.delete(f"/api/cases/{case.id}/consents/public_page")
    assert response.status_code == 200
    assert response.get_json()["active"] is False
    assert response.get_json()["withdrawn_at"] is not None
    [listed] = browser.get(f"/api/cases/{case.id}/consents").get_json()["items"]
    assert listed["active"] is False


def test_withdrawing_hospital_verification_cancels_a_case_still_in_checks(
    browser_for: BrowserFor, db_session
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.PENDING_CONFIRMATION)
    ConsentFactory(case=case, scope="hospital_verification")
    browser_for(case.created_by).delete(f"/api/cases/{case.id}/consents/hospital_verification")
    db_session.refresh(case)
    assert case.status == CaseStatus.CANCELLED


def test_withdrawing_what_was_never_given_or_misnamed_is_404(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    browser = browser_for(case.created_by)
    for scope in ("public_page", "not_a_scope"):
        response = browser.delete(f"/api/cases/{case.id}/consents/{scope}")
        assert response.status_code == 404
        assert _error(response)["code"] == "consent_not_found"


# --- Access ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", ""), ("post", ""), ("delete", "/public_page")],
)
def test_anonymous_callers_get_401(browser: Browser, method: str, path: str) -> None:
    case = CaseFactory()
    response = getattr(browser, method)(f"/api/cases/{case.id}/consents{path}")
    assert response.status_code == 401


def test_other_roles_are_refused(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    admin = browser_for(UserFactory(role=UserRole.ADMIN))
    assert admin.get(f"/api/cases/{case.id}/consents").status_code == 403


def test_someone_elses_case_is_a_404_for_every_route(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    ConsentFactory(case=case, scope="public_page")
    stranger = browser_for(UserFactory())
    for response in (
        stranger.get(f"/api/cases/{case.id}/consents"),
        stranger.post(f"/api/cases/{case.id}/consents", _body(scope="receipt_sharing")),
        stranger.delete(f"/api/cases/{case.id}/consents/public_page"),
    ):
        assert response.status_code == 404
        assert _error(response)["code"] == "case_not_found"
    assert case.active_consent("public_page") is not None  # type: ignore[arg-type]


def test_the_routes_are_documented(client) -> None:  # type: ignore[no-untyped-def]
    paths = client.get("/api/openapi.json").get_json()["paths"]
    assert {"/api/hospitals", "/api/consents/texts", "/api/cases/{case_id}/consents"} <= set(paths)
    assert "/api/cases/{case_id}/consents/{scope}" in paths
