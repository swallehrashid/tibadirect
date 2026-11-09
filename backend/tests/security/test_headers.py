"""Headers and caching on every kind of answer: success, refusal, error and the web app itself."""

import pytest

from app.models.enums import UserRole
from tests.browser import Browser
from tests.factories import CaseFactory, DonationFactory, UserFactory
from tests.security.test_route_inventory import concrete

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def _assert_secure(response) -> None:  # type: ignore[no-untyped-def]
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("get", "/api/public/config", 200),
        ("get", "/api/auth/me", 401),  # refused
        ("get", "/api/no-such-route", 404),
        ("get", "/api/public/cases/does-not-exist", 404),
        ("delete", "/api/public/config", 405),
        ("post", "/api/auth/verify", 422),  # invalid input
    ],
)
def test_every_kind_of_api_answer_carries_the_security_headers(
    browser: Browser, method: str, path: str, status: int
) -> None:
    response = getattr(browser, method)(path)
    assert response.status_code == status
    _assert_secure(response)


def test_an_error_never_shows_a_stack_trace_or_a_file_path(app, browser: Browser) -> None:  # type: ignore[no-untyped-def]
    response = browser.get("/api/_test/boom")
    assert response.status_code == 500
    text = response.get_data(as_text=True)
    assert "Traceback" not in text
    assert "/home/" not in text
    assert ".py" not in text
    assert response.get_json()["error"]["code"] == "internal_error"
    _assert_secure(response)


def test_nothing_the_api_answers_is_kept_by_a_browser_or_a_proxy(browser_for) -> None:  # type: ignore[no-untyped-def]
    """Private answers (a case, a bill, a receipt) must not be stored: shared phones are common."""
    owner = UserFactory(role=UserRole.FAMILY)
    case = CaseFactory(created_by=owner)
    donation = DonationFactory(case=case, status="captured")
    browser = browser_for(owner)
    for path in (
        "/api/auth/me",
        "/api/cases",
        f"/api/cases/{case.id}",
        f"/api/cases/{case.id}/documents",
        f"/api/donations/receipts/{donation.receipt_token}",
        "/api/public/cases",
        "/api/public/config",
        "/api/auth/csrf",
        f"/api/cases/{concrete('<uuid:x>')}",  # not found
    ):
        response = browser.get(path)
        assert response.headers.get("Cache-Control") == "no-store", (path, response.status_code)
