"""API foundation: OpenAPI docs, CSRF, security headers, CORS, rate limits, public config."""

import base64
import os
from collections.abc import Iterator

import pytest
from flask import Flask, request, session
from flask.testing import FlaskClient
from flask_talisman import Talisman

from app.config import get_config
from app.extensions import limiter
from app.security import web
from tests.factories import FacilityRegisterEntryFactory


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


@pytest.fixture(autouse=True)
def _reset_limits(app: Flask) -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


# --- OpenAPI -----------------------------------------------------------------------------------


def test_openapi_document_describes_routes_and_security(client: FlaskClient) -> None:
    spec = client.get("/api/openapi.json").get_json()
    assert spec["openapi"] == "3.0.3"
    assert "/api/auth/csrf" in spec["paths"]
    assert "/api/public/config" in spec["paths"]
    schemes = spec["components"]["securitySchemes"]
    assert schemes["sessionCookie"]["name"] == "td_session"
    assert schemes["csrfToken"]["name"] == "X-CSRFToken"


def test_swagger_ui_is_served_with_a_policy_that_lets_it_load(client: FlaskClient) -> None:
    response = client.get("/api/docs")
    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in policy
    assert "frame-ancestors 'none'" in policy


# --- CSRF --------------------------------------------------------------------------------------


def test_csrf_endpoint_returns_a_token_and_is_not_cacheable(client: FlaskClient) -> None:
    response = client.get("/api/auth/csrf")
    assert response.status_code == 200
    assert len(response.get_json()["csrf_token"]) > 20
    assert response.headers["Cache-Control"] == "no-store"


def test_post_without_a_token_is_refused_with_the_error_envelope(
    client: FlaskClient, csrf_on: None
) -> None:
    response = client.post("/api/_test/echo", json={"name": "ok", "amount": 1})
    assert response.status_code == 403
    assert _error(response)["code"] == "csrf_failed"


def test_post_with_a_wrong_token_is_refused(client: FlaskClient, csrf_on: None) -> None:
    client.get("/api/auth/csrf")
    response = client.post(
        "/api/_test/echo", json={"name": "ok", "amount": 1}, headers={"X-CSRFToken": "nope"}
    )
    assert response.status_code == 403
    assert _error(response)["code"] == "csrf_failed"


def test_post_with_the_issued_token_succeeds(client: FlaskClient, csrf_on: None) -> None:
    token = client.get("/api/auth/csrf").get_json()["csrf_token"]
    response = client.post(
        "/api/_test/echo", json={"name": "ok", "amount": 1}, headers={"X-CSRFToken": token}
    )
    assert response.status_code == 200


def test_safe_methods_need_no_token(client: FlaskClient, csrf_on: None) -> None:
    assert client.get("/api/health").status_code in {200, 503}


def test_a_token_from_another_session_is_refused(app: Flask, csrf_on: None) -> None:
    token = app.test_client().get("/api/auth/csrf").get_json()["csrf_token"]
    stranger = app.test_client()
    stranger.get("/api/auth/csrf")
    response = stranger.post(
        "/api/_test/echo", json={"name": "ok", "amount": 1}, headers={"X-CSRFToken": token}
    )
    assert response.status_code == 403


def test_session_cookie_is_http_only_and_same_site(client: FlaskClient) -> None:
    response = client.get("/api/auth/csrf")
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("td_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


# --- Security headers --------------------------------------------------------------------------


def test_every_response_carries_the_security_headers(client: FlaskClient) -> None:
    headers = client.get("/api/public/config").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    policy = headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "https://www.paypal.com" in policy
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]


def test_hsts_is_only_sent_in_production(client: FlaskClient) -> None:
    assert "Strict-Transport-Security" not in client.get("/api/public/config").headers


# --- CORS --------------------------------------------------------------------------------------


def test_the_local_vite_origin_may_call_the_api_with_credentials(client: FlaskClient) -> None:
    response = client.get("/api/public/config", headers={"Origin": "http://localhost:5173"})
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_preflight_allows_the_csrf_header(client: FlaskClient) -> None:
    response = client.options(
        "/api/_test/echo",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrftoken,content-type",
        },
    )
    assert response.status_code == 200
    assert "x-csrftoken" in response.headers["Access-Control-Allow-Headers"].lower()


def test_requests_without_an_origin_get_no_cors_headers(client: FlaskClient) -> None:
    assert "Access-Control-Allow-Origin" not in client.get("/api/public/config").headers


def test_other_origins_get_no_cors_headers(client: FlaskClient) -> None:
    response = client.get("/api/public/config", headers={"Origin": "https://evil.test"})
    assert "Access-Control-Allow-Origin" not in response.headers


# --- Rate limits -------------------------------------------------------------------------------


def test_the_limit_returns_the_error_envelope_with_retry_after(client: FlaskClient) -> None:
    assert client.get("/api/_test/limited").status_code == 200
    assert client.get("/api/_test/limited").status_code == 200
    response = client.get("/api/_test/limited")
    assert response.status_code == 429
    assert _error(response)["code"] == "rate_limited"
    assert int(response.headers["Retry-After"]) > 0


def test_limits_are_counted_per_client_address(app: Flask) -> None:
    first = app.test_client()
    second = app.test_client()
    for _ in range(2):
        first.get("/api/_test/limited", environ_overrides={"REMOTE_ADDR": "10.0.0.1"})
    blocked = first.get("/api/_test/limited", environ_overrides={"REMOTE_ADDR": "10.0.0.1"})
    other = second.get("/api/_test/limited", environ_overrides={"REMOTE_ADDR": "10.0.0.2"})
    assert blocked.status_code == 429
    assert other.status_code == 200


# --- Public config -----------------------------------------------------------------------------


def test_public_config_exposes_only_what_the_browser_needs(client: FlaskClient, app: Flask) -> None:
    body = client.get("/api/public/config").get_json()
    assert body == {
        "paypal_client_id": app.config["PAYPAL_CLIENT_ID"],
        "paypal_mode": "mock",
        "currency": "USD",
        "demo_banner": app.config["DEMO_BANNER"],
        "features": {"agent": False, "appeal_checker": True},
    }
    assert "secret" not in str(body).lower()


# --- Pagination --------------------------------------------------------------------------------


@pytest.fixture
def register_entries(db_session) -> int:  # type: ignore[no-untyped-def]
    for index in range(25):
        FacilityRegisterEntryFactory(register_code=f"PG-{index:03d}", name=f"Pager {index}")
    return 25


def test_first_page_defaults_to_twenty(client: FlaskClient, register_entries: int) -> None:
    body = client.get("/api/_test/pages").get_json()
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] >= register_entries
    assert len(body["items"]) == 20
    assert set(body["items"][0]) == {"register_code", "name"}


def test_pages_do_not_overlap_and_cover_everything(
    client: FlaskClient, register_entries: int
) -> None:
    seen: list[str] = []
    page = 1
    while True:
        body = client.get(f"/api/_test/pages?page={page}&page_size=10").get_json()
        seen.extend(item["register_code"] for item in body["items"])
        if len(seen) >= body["total"]:
            break
        page += 1
    assert len(seen) == len(set(seen)) == body["total"]
    assert seen == sorted(seen)


def test_a_page_past_the_end_is_empty_not_an_error(
    client: FlaskClient, register_entries: int
) -> None:
    body = client.get("/api/_test/pages?page=999").get_json()
    assert body["items"] == []
    assert body["total"] >= register_entries


@pytest.mark.parametrize(
    "query", ["page=0", "page=-1", "page_size=0", "page_size=101", "page=abc", "page_size=1.5"]
)
def test_invalid_paging_is_a_validation_error(client: FlaskClient, query: str) -> None:
    response = client.get(f"/api/_test/pages?{query}")
    assert response.status_code == 422
    assert _error(response)["code"] == "validation_error"


def test_the_maximum_page_size_is_accepted(client: FlaskClient, register_entries: int) -> None:
    assert client.get("/api/_test/pages?page_size=100").status_code == 200


def test_page_schemas_are_named_after_their_items(client: FlaskClient) -> None:
    schemas = client.get("/api/openapi.json").get_json()["components"]["schemas"]
    assert set(schemas["RegisterEntryPage"]["properties"]) == {
        "items",
        "page",
        "page_size",
        "total",
    }


# --- Production hardening ----------------------------------------------------------------------
# These build a bare Flask app, never a second real one: the extensions are shared objects, and a
# production app would leave its settings behind for every test that runs after it.


@pytest.fixture
def production_flask() -> Flask:
    bare = Flask("production-check")
    bare.config.update(
        APP_ENV="production",
        SECRET_KEY="only-for-this-test",
        SESSION_COOKIE_SECURE=True,
        PROXY_FIX_COUNT=1,
        CORS_ORIGINS=[],
    )
    web.apply_proxy_fix(bare)
    web.init_security_headers(bare, Talisman())
    web.init_cors(bare)

    @bare.get("/api/ping")
    def ping() -> dict:
        session["seen"] = True
        return {"ok": True}

    return bare


def test_production_redirects_http_to_https(production_flask: Flask) -> None:
    response = production_flask.test_client().get("/api/ping")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://")


def test_production_sends_hsts_and_a_secure_cookie(production_flask: Flask) -> None:
    response = production_flask.test_client().get(
        "/api/ping", base_url="https://tibadirect.example"
    )
    assert response.status_code == 200
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]
    assert "includeSubDomains" in response.headers["Strict-Transport-Security"]
    assert "Secure" in response.headers["Set-Cookie"]


def test_production_trusts_the_forwarded_scheme_from_one_proxy(production_flask: Flask) -> None:
    response = production_flask.test_client().get(
        "/api/ping", headers={"X-Forwarded-Proto": "https"}
    )
    assert response.status_code == 200


def test_production_allows_no_cross_origin_callers(production_flask: Flask) -> None:
    response = production_flask.test_client().get(
        "/api/ping",
        base_url="https://tibadirect.example",
        headers={"Origin": "http://localhost:5173"},
    )
    assert "Access-Control-Allow-Origin" not in response.headers


def test_one_trusted_proxy_supplies_the_client_address() -> None:
    bare = Flask("proxy")
    bare.config.update(PROXY_FIX_COUNT=1)
    web.apply_proxy_fix(bare)

    @bare.get("/who")
    def who() -> dict:
        return {"ip": request.remote_addr}

    response = bare.test_client().get(
        "/who",
        headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1"},
        environ_overrides={"REMOTE_ADDR": "10.0.0.2"},
    )
    # Only the last hop is trusted; anything a client put earlier in the chain is ignored.
    assert response.get_json() == {"ip": "10.0.0.1"}


def test_without_a_proxy_forwarded_addresses_are_ignored(app: Flask) -> None:
    client = app.test_client()
    statuses = [
        client.get(
            "/api/_test/limited",
            headers={"X-Forwarded-For": f"203.0.113.{index}"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.9"},
        ).status_code
        for index in range(3)
    ]
    assert statuses == [200, 200, 429]


def test_production_config_is_secure_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "a-real-secret-for-this-test-only")
    monkeypatch.setenv("HASH_PEPPER", "a-real-pepper-for-this-test-only")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@localhost:5432/db")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "bucket-user-for-this-test")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "bucket-password-for-this-test")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    config = get_config("production")
    assert config.SESSION_COOKIE_SECURE is True
    assert config.CORS_ORIGINS == []
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    assert get_config("production").CORS_ORIGINS == ["https://a.example", "https://b.example"]


def test_the_health_probe_is_not_redirected_to_https_but_everything_else_is(
    production_flask: Flask,
) -> None:
    production_flask.add_url_rule(
        "/api/health", endpoint="health.Health", view_func=lambda: {"status": "ok"}
    )
    web.exempt_health_from_https_redirect(production_flask)
    client = production_flask.test_client()
    probe = client.get("/api/health")
    assert (probe.status_code, probe.get_json()) == (200, {"status": "ok"})
    assert client.get("/api/ping").status_code == 302


def test_the_real_app_marks_the_health_route_as_exempt(app: Flask) -> None:
    assert app.view_functions["health.Health"].talisman_view_options["force_https"] is False  # type: ignore[attr-defined]
