"""Email-code sign-in over HTTP: no enumeration, sessions, profile and rate limits."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import pytest

from app.models.enums import StaffRole, UserRole
from app.services import mail_service
from tests.browser import Browser
from tests.factories import HospitalStaffFactory, UserFactory

BrowserFor = Callable[..., Browser]


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


# --- Requesting a code -------------------------------------------------------------------------


def test_request_code_is_accepted_and_emails_a_code(browser: Browser) -> None:
    response = browser.post("/api/auth/request-code", {"email": "mama@example.test"})
    assert response.status_code == 202
    [message] = mail_service.outbox
    assert message.to == "mama@example.test"


def test_the_code_never_appears_in_the_response(browser: Browser) -> None:
    response = browser.post("/api/auth/request-code", {"email": "mama@example.test"})
    code = mail_service.outbox[0].text.split("is ")[1][:6]
    assert code not in response.get_data(as_text=True)


def test_known_unknown_and_switched_off_addresses_look_identical(browser: Browser) -> None:
    UserFactory(email="known@example.test")
    UserFactory(email="off@example.test", is_active=False)
    responses = [
        browser.post("/api/auth/request-code", {"email": email})
        for email in ("known@example.test", "unknown@example.test", "off@example.test")
    ]
    assert {r.status_code for r in responses} == {202}
    assert len({r.get_data() for r in responses}) == 1
    assert len({r.headers["Content-Type"] for r in responses}) == 1
    assert [m.to for m in mail_service.outbox] == ["known@example.test", "unknown@example.test"]


@pytest.mark.parametrize("body", [{}, {"email": ""}, {"email": "not-an-email"}, {"email": 5}])
def test_request_code_validates_the_address(browser: Browser, body: dict) -> None:
    response = browser.post("/api/auth/request-code", body)
    assert response.status_code == 422
    assert _error(response)["code"] == "validation_error"
    assert not mail_service.outbox


def test_request_code_needs_a_csrf_token(app, db_session, csrf_on) -> None:  # type: ignore[no-untyped-def]
    response = app.test_client().post("/api/auth/request-code", json={"email": "a@example.test"})
    assert response.status_code == 403
    assert _error(response)["code"] == "csrf_failed"


def test_sixth_request_for_one_address_is_limited_even_from_new_addresses(
    app, db_session, csrf_on
) -> None:  # type: ignore[no-untyped-def]
    for index in range(5):
        caller = Browser(app.test_client(), f"203.0.113.{index + 1}")
        assert (
            caller.post("/api/auth/request-code", {"email": "mama@example.test"}).status_code == 202
        )
    response = Browser(app.test_client(), "203.0.113.99").post(
        "/api/auth/request-code", {"email": "Mama@Example.test"}
    )
    assert response.status_code == 429
    assert _error(response)["code"] == "rate_limited"
    assert int(response.headers["Retry-After"]) > 0
    assert len(mail_service.outbox) == 5


def test_sixth_request_from_one_address_is_limited_for_any_email(browser: Browser) -> None:
    for index in range(5):
        status = browser.post("/api/auth/request-code", {"email": f"p{index}@example.test"})
        assert status.status_code == 202
    assert browser.post("/api/auth/request-code", {"email": "p9@example.test"}).status_code == 429


# --- Verifying and sessions --------------------------------------------------------------------


def test_signing_in_creates_the_account_and_a_secure_session(browser: Browser) -> None:
    response = browser.sign_in_by_email("Amina.Otieno@example.test")
    assert response.status_code == 200
    user = response.get_json()["user"]
    assert user["email"] == "amina.otieno@example.test"
    assert user["role"] == "family"
    assert user["display_name"] == "Amina Otieno"
    assert user["preferred_language"] == "en"
    assert user["hospital"] is None

    cookie = next(
        value for key, value in response.headers if key == "Set-Cookie" and "td_session" in value
    )
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    expires = parsedate_to_datetime(
        next(part.split("=", 1)[1] for part in cookie.split("; ") if part.startswith("Expires="))
    )
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7, minutes=1)


def test_me_returns_the_signed_in_user(browser: Browser) -> None:
    browser.sign_in_by_email("amina@example.test")
    me = browser.get("/api/auth/me").get_json()
    assert me["email"] == "amina@example.test"
    assert set(me) == {"id", "email", "display_name", "role", "preferred_language", "hospital"}


def test_wrong_code_is_a_401_with_one_generic_message(browser: Browser) -> None:
    browser.post("/api/auth/request-code", {"email": "mama@example.test"})
    code = mail_service.outbox[0].text.split("is ")[1][:6]
    wrong = "000000" if code != "000000" else "111111"
    bad = browser.post("/api/auth/verify", {"email": "mama@example.test", "code": wrong})
    nothing = browser.post("/api/auth/verify", {"email": "nobody@example.test", "code": wrong})
    assert bad.status_code == nothing.status_code == 401
    assert _error(bad)["code"] == "invalid_code"
    assert _error(bad)["message"] == _error(nothing)["message"]
    assert browser.get("/api/auth/me").status_code == 401


def test_five_wrong_codes_lock_it_even_for_the_right_one(app, db_session, csrf_on) -> None:  # type: ignore[no-untyped-def]
    first = Browser(app.test_client(), "203.0.113.1")
    first.post("/api/auth/request-code", {"email": "mama@example.test"})
    code = mail_service.outbox[0].text.split("is ")[1][:6]
    wrong = "000000" if code != "000000" else "111111"
    for index in range(5):
        guess = Browser(app.test_client(), f"203.0.113.{index + 10}")
        assert (
            guess.post(
                "/api/auth/verify", {"email": "mama@example.test", "code": wrong}
            ).status_code
            == 401
        )
    late = Browser(app.test_client(), "203.0.113.50")
    response = late.post("/api/auth/verify", {"email": "mama@example.test", "code": code})
    assert response.status_code == 401
    assert late.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "", "12 456"])
def test_verify_rejects_a_malformed_code(browser: Browser, code: str) -> None:
    response = browser.post("/api/auth/verify", {"email": "mama@example.test", "code": code})
    assert response.status_code == 422


def test_verify_attempts_are_rate_limited(browser: Browser) -> None:
    statuses = [
        browser.post(
            "/api/auth/verify", {"email": f"v{index}@example.test", "code": "123456"}
        ).status_code
        for index in range(11)
    ]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


def test_signing_in_replaces_the_session_so_a_planted_one_is_useless(browser: Browser) -> None:
    old_token = browser.refresh_csrf()
    browser.sign_in_by_email("mama@example.test")
    stale = browser.client.post("/api/auth/logout", headers={"X-CSRFToken": old_token})
    assert stale.status_code == 403
    assert browser.get("/api/auth/me").status_code == 200


def test_logout_ends_the_session(browser: Browser) -> None:
    browser.sign_in_by_email("mama@example.test")
    assert browser.post("/api/auth/logout").status_code == 204
    assert browser.get("/api/auth/me").status_code == 401


def test_logout_when_signed_out_is_harmless(browser: Browser) -> None:
    assert browser.post("/api/auth/logout").status_code == 204


def test_a_switched_off_account_loses_its_session_at_once(
    browser_for: BrowserFor, db_session
) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory()
    browser = browser_for(user)
    assert browser.get("/api/auth/me").status_code == 200
    user.is_active = False
    db_session.flush()
    assert browser.get("/api/auth/me").status_code == 401


def test_an_existing_staff_account_signs_in_as_staff_with_its_hospital(browser: Browser) -> None:
    staff = HospitalStaffFactory(
        user__email="clerk@hospital.test", staff_role=StaffRole.BILLING_SUPERVISOR
    )
    response = browser.sign_in_by_email("clerk@hospital.test")
    user = response.get_json()["user"]
    assert user["role"] == "hospital_staff"
    assert user["hospital"] == {
        "id": str(staff.hospital_id),
        "display_name": staff.hospital.display_name,
        "county": staff.hospital.county,
        "staff_role": "billing_supervisor",
    }


def test_an_admin_account_signs_in_as_admin(browser: Browser) -> None:
    UserFactory(email="admin@tibadirect.test", role=UserRole.ADMIN)
    user = browser.sign_in_by_email("admin@tibadirect.test").get_json()["user"]
    assert user["role"] == "admin"
    assert user["hospital"] is None


def test_the_email_arrives_in_the_users_language(browser: Browser) -> None:
    UserFactory(email="mama@example.test", preferred_language="sw")
    browser.post("/api/auth/request-code", {"email": "mama@example.test"})
    assert mail_service.outbox[0].language == "sw"


# --- Profile -----------------------------------------------------------------------------------


def test_me_needs_a_session(browser: Browser) -> None:
    response = browser.get("/api/auth/me")
    assert response.status_code == 401
    assert _error(response)["code"] == "unauthenticated"


def test_update_name_and_language(browser_for: BrowserFor) -> None:
    browser = browser_for(UserFactory(display_name="Old Name"))
    response = browser.patch(
        "/api/auth/me", {"display_name": "  Amina Otieno  ", "preferred_language": "sw"}
    )
    assert response.status_code == 200
    assert response.get_json()["display_name"] == "Amina Otieno"
    assert browser.get("/api/auth/me").get_json()["preferred_language"] == "sw"


def test_an_empty_update_changes_nothing(browser_for: BrowserFor) -> None:
    browser = browser_for(UserFactory(display_name="Same"))
    assert browser.patch("/api/auth/me", {}).get_json()["display_name"] == "Same"


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "x" * 81},
        {"preferred_language": "fr"},
        {"role": "admin"},
        {"email": "other@example.test"},
        {"is_active": False},
    ],
)
def test_update_rejects_bad_or_forbidden_fields(browser_for: BrowserFor, body: dict) -> None:
    browser = browser_for(UserFactory())
    response = browser.patch("/api/auth/me", body)
    assert response.status_code == 422


def test_a_family_member_cannot_make_themselves_admin(browser_for: BrowserFor, db_session) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory()
    browser = browser_for(user)
    browser.patch("/api/auth/me", {"role": "admin"})
    db_session.refresh(user)
    assert user.role == UserRole.FAMILY


def test_update_needs_a_session_and_a_token(browser: Browser, app, csrf_on) -> None:  # type: ignore[no-untyped-def]
    assert browser.patch("/api/auth/me", {"display_name": "x"}).status_code == 401
    bare = app.test_client().patch("/api/auth/me", json={"display_name": "x"})
    assert bare.status_code == 403


def test_openapi_lists_the_auth_routes_with_security(client) -> None:  # type: ignore[no-untyped-def]
    spec = client.get("/api/openapi.json").get_json()
    for path in ("/api/auth/request-code", "/api/auth/verify", "/api/auth/logout", "/api/auth/me"):
        assert path in spec["paths"]
    assert spec["paths"]["/api/auth/me"]["get"]["security"] == [{"sessionCookie": []}]
    assert "401" in spec["paths"]["/api/auth/me"]["get"]["responses"]
