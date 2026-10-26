"""Every route says who may call it, and the rule holds.

``POLICY`` is the one place that lists each API route and its gate. A new route that is not listed
fails the first test, so nobody can add an endpoint without deciding who may use it. The other
tests then walk the whole table: signed out, the wrong role, and no CSRF token.
"""

import re
import uuid
from collections.abc import Callable

import pytest
from flask import Flask

from app.models.enums import UserRole
from tests.browser import Browser
from tests.factories import HospitalStaffFactory, UserFactory

BrowserFor = Callable[..., Browser]

PUBLIC = "public"  # anyone, signed in or not
LOGIN = "login"  # any signed-in account
FAMILY = "family"
STAFF = "hospital_staff"
ADMIN = "admin"
WEBHOOK = "webhook"  # no session; the PayPal signature is the credential

POLICY: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): PUBLIC,
    ("GET", "/api/docs"): PUBLIC,
    ("GET", "/api/openapi.json"): PUBLIC,
    # --- Sign-in --------------------------------------------------------------------------------
    ("GET", "/api/auth/csrf"): PUBLIC,
    ("POST", "/api/auth/request-code"): PUBLIC,
    ("POST", "/api/auth/verify"): PUBLIC,
    ("POST", "/api/auth/logout"): PUBLIC,
    ("GET", "/api/auth/me"): LOGIN,
    ("PATCH", "/api/auth/me"): LOGIN,
    # --- Public pages and the donation flow (a donor needs no account) ---------------------------
    ("GET", "/api/consents/texts"): PUBLIC,
    ("GET", "/api/hospitals"): PUBLIC,
    ("GET", "/api/public/cases"): PUBLIC,
    ("GET", "/api/public/cases/<string:slug>"): PUBLIC,
    ("GET", "/api/public/cases/<string:slug>/ledger"): PUBLIC,
    ("GET", "/api/public/cases/<string:slug>/ledger/verify"): PUBLIC,
    ("POST", "/api/public/checker/appeal"): PUBLIC,
    ("GET", "/api/public/config"): PUBLIC,
    ("GET", "/api/public/fx"): PUBLIC,
    ("GET", "/api/public/stats"): PUBLIC,
    ("POST", "/api/donations/orders"): PUBLIC,
    ("POST", "/api/donations/orders/<string:order_id>/capture"): PUBLIC,
    ("GET", "/api/donations/receipts/<string:receipt_token>"): PUBLIC,
    ("POST", "/api/webhooks/paypal"): WEBHOOK,
    # --- A family's own cases --------------------------------------------------------------------
    ("GET", "/api/cases"): FAMILY,
    ("POST", "/api/cases"): FAMILY,
    ("GET", "/api/cases/<uuid:case_id>"): FAMILY,
    ("PATCH", "/api/cases/<uuid:case_id>"): FAMILY,
    ("DELETE", "/api/cases/<uuid:case_id>"): FAMILY,
    ("POST", "/api/cases/<uuid:case_id>/cancel"): FAMILY,
    ("POST", "/api/cases/<uuid:case_id>/review-request"): FAMILY,
    ("POST", "/api/cases/<uuid:case_id>/submit"): FAMILY,
    ("GET", "/api/cases/<uuid:case_id>/triage"): FAMILY,
    ("POST", "/api/cases/<uuid:case_id>/final-bill"): FAMILY,
    ("GET", "/api/cases/<uuid:case_id>/consents"): FAMILY,
    ("POST", "/api/cases/<uuid:case_id>/consents"): FAMILY,
    ("DELETE", "/api/cases/<uuid:case_id>/consents/<string:scope>"): FAMILY,
    ("GET", "/api/cases/<uuid:case_id>/documents"): FAMILY,
    ("POST", "/api/cases/<uuid:case_id>/documents"): FAMILY,
    ("DELETE", "/api/cases/<uuid:case_id>/documents/<uuid:document_id>"): FAMILY,
    # --- Hospital billing staff ------------------------------------------------------------------
    ("GET", "/api/hospital/queue"): STAFF,
    ("GET", "/api/hospital/settlements"): STAFF,
    ("GET", "/api/hospital/cases/<uuid:case_id>"): STAFF,
    ("POST", "/api/hospital/cases/<uuid:case_id>/otp"): STAFF,
    ("POST", "/api/hospital/cases/<uuid:case_id>/decision"): STAFF,
    ("POST", "/api/hospital/confirmations/<uuid:confirmation_id>/approve"): STAFF,
    # --- Administrators --------------------------------------------------------------------------
    ("GET", "/api/admin/jobs"): ADMIN,
    ("POST", "/api/admin/jobs/<string:job_id>/retry"): ADMIN,
    ("GET", "/api/admin/ledger/verify"): ADMIN,
    ("GET", "/api/admin/stats"): ADMIN,
    ("GET", "/api/admin/review-items"): ADMIN,
    ("POST", "/api/admin/review-items/<uuid:item_id>/resolve"): ADMIN,
    ("GET", "/api/admin/cases"): ADMIN,
    ("GET", "/api/admin/cases/<uuid:case_id>"): ADMIN,
    ("POST", "/api/admin/cases/<uuid:case_id>/freeze"): ADMIN,
    ("POST", "/api/admin/cases/<uuid:case_id>/settlements/run"): ADMIN,
    ("GET", "/api/admin/audit-log"): ADMIN,
    ("POST", "/api/admin/agent/ask"): ADMIN,
    ("GET", "/api/admin/agent/actions"): ADMIN,
    ("POST", "/api/admin/agent/actions/<uuid:action_id>/approve"): ADMIN,
    ("POST", "/api/admin/agent/actions/<uuid:action_id>/reject"): ADMIN,
}

ROLE_OF = {FAMILY: UserRole.FAMILY, STAFF: UserRole.HOSPITAL_STAFF, ADMIN: UserRole.ADMIN}
WRITES = {"POST", "PATCH", "PUT", "DELETE"}


@pytest.fixture(autouse=True)
def _agent_switched_on(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag off the agent routes answer 404 to everyone, which hides their gates."""
    monkeypatch.setitem(app.config, "AGENT_ENABLED", True)


def api_routes(app: Flask) -> list[tuple[str, str]]:
    found = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        found += [(method, rule.rule) for method in rule.methods - {"HEAD", "OPTIONS"}]
    return sorted(found)


def concrete(rule: str) -> str:
    """The rule with an unused but well-formed value in each placeholder."""
    return re.sub(r"<(?:uuid:)?(\w+)>|<string:(\w+)>", lambda m: _value(m), rule)


def _value(match: re.Match[str]) -> str:
    text = match.group(0)
    return str(uuid.uuid4()) if text.startswith("<uuid:") else "x" * 12


def gated(role: str) -> list[tuple[str, str]]:
    return sorted(route for route, gate in POLICY.items() if gate == role)


def test_every_route_is_listed_with_a_gate(app: Flask) -> None:
    listed = set(POLICY)
    actual = {route for route in api_routes(app) if not route[1].startswith("/api/_test/")}
    assert actual - listed == set(), "a route has no gate in POLICY: decide who may call it"
    assert listed - actual == set(), "POLICY lists a route that no longer exists"


def test_the_documented_gates_match_the_policy(client) -> None:  # type: ignore[no-untyped-def]
    """What the OpenAPI document tells integrators matches what this table says."""
    spec = client.get("/api/openapi.json").get_json()
    for (method, rule), gate in POLICY.items():
        if rule.startswith("/api/docs") or rule == "/api/openapi.json":
            continue
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule)
        operation = spec["paths"][path][method.lower()]
        assert bool(operation.get("security")) == (gate in (LOGIN, FAMILY, STAFF, ADMIN)), (
            method,
            path,
        )
        assert ("403" in operation["responses"]) == (gate in (FAMILY, STAFF, ADMIN)), (method, path)


@pytest.mark.parametrize(
    ("method", "rule"), [r for g in (LOGIN, FAMILY, STAFF, ADMIN) for r in gated(g)]
)
def test_a_signed_out_caller_is_refused_on_every_protected_route(
    browser: Browser, method: str, rule: str
) -> None:
    response = getattr(browser, method.lower())(concrete(rule))
    assert response.status_code == 401, (method, rule, response.get_data(as_text=True)[:200])
    assert response.get_json()["error"]["code"] == "unauthenticated"


def _other_roles(gate: str) -> list[UserRole]:
    return [role for role in ROLE_OF.values() if role != ROLE_OF[gate]]


def _signed_in_as(browser_for: BrowserFor, role: UserRole) -> Browser:
    user = UserFactory(role=role)
    if role == UserRole.HOSPITAL_STAFF:
        HospitalStaffFactory(user=user)
    return browser_for(user)


@pytest.mark.parametrize(
    ("method", "rule", "role"),
    [
        (method, rule, role)
        for gate in (FAMILY, STAFF, ADMIN)
        for method, rule in gated(gate)
        for role in _other_roles(gate)
    ],
)
def test_the_wrong_role_is_refused_on_every_role_gated_route(
    browser_for: BrowserFor, method: str, rule: str, role: UserRole
) -> None:
    browser = _signed_in_as(browser_for, role)
    response = getattr(browser, method.lower())(concrete(rule))
    assert response.status_code == 403, (method, rule, role, response.get_data(as_text=True)[:200])
    assert response.get_json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(
    ("method", "rule"),
    [
        route
        for route, gate in sorted(POLICY.items())
        if route[0] in WRITES and gate not in (WEBHOOK,)
    ],
)
def test_every_write_needs_the_csrf_token(browser_for: BrowserFor, method: str, rule: str) -> None:
    """A write without the token is refused whoever sends it, before any other check."""
    gate = POLICY[(method, rule)]
    browser = _signed_in_as(browser_for, ROLE_OF.get(gate, UserRole.FAMILY))
    url = concrete(rule)
    response = browser.client.open(
        url,
        method=method,
        json={},
        environ_overrides={"REMOTE_ADDR": browser.remote_addr},
    )
    assert response.status_code == 403, (method, rule, response.get_data(as_text=True)[:200])
    assert response.get_json()["error"]["code"] == "csrf_failed"

    forged = browser.client.open(
        url,
        method=method,
        json={},
        headers={"X-CSRFToken": "not-the-token"},
        environ_overrides={"REMOTE_ADDR": browser.remote_addr},
    )
    assert forged.status_code == 403
    assert forged.get_json()["error"]["code"] == "csrf_failed"


def test_the_webhook_takes_no_csrf_token_but_refuses_unsigned_calls(browser: Browser) -> None:
    """A webhook comes from PayPal, which has no session, so the signature is its credential."""
    response = browser.client.post("/api/webhooks/paypal", json={"id": "WH-1"})
    assert response.status_code != 403 or response.get_json()["error"]["code"] != "csrf_failed"
    assert response.status_code in (400, 401, 422)


def test_with_the_agent_off_its_routes_do_not_exist_for_anyone(
    app: Flask, monkeypatch: pytest.MonkeyPatch, browser_for: BrowserFor
) -> None:
    monkeypatch.setitem(app.config, "AGENT_ENABLED", False)
    admin = browser_for(UserFactory(role=UserRole.ADMIN))
    for method, rule in gated(ADMIN):
        if "/agent/" in rule:
            response = getattr(admin, method.lower())(concrete(rule))
            assert response.status_code == 404, (method, rule)
            assert response.get_json()["error"]["code"] == "feature_disabled"
