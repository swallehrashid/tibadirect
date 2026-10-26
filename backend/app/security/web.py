"""Browser-facing security: response headers, CORS, proxy headers and the OpenAPI security schemes.

CSRF protection and rate limiting are extension instances in ``app.extensions``.
"""

import functools
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from flask import Flask
from flask_cors import CORS
from flask_smorest import Api
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix

# The PayPal JS SDK needs these hosts for scripts, frames, connections and images.
_PAYPAL_HOSTS = ["https://www.paypal.com", "https://*.paypal.com", "https://*.paypalobjects.com"]

CONTENT_SECURITY_POLICY: dict[str, Any] = {
    "default-src": "'self'",
    "script-src": ["'self'", *_PAYPAL_HOSTS],
    "frame-src": ["'self'", *_PAYPAL_HOSTS],
    "connect-src": ["'self'", *_PAYPAL_HOSTS],
    "img-src": ["'self'", "data:", *_PAYPAL_HOSTS],
    # Fonts are our own files, plus the icon font AG Grid embeds in its stylesheet as a data: URI.
    "font-src": ["'self'", "data:"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "object-src": "'none'",
}


def storage_origins(config: Mapping[str, Any]) -> list[str]:
    """Where the browser fetches bill files from: the origin of the signed links.

    Links are path-style, so the origin is the storage endpoint itself (the public one when the
    browser reaches storage at a different address than the server does). With no endpoint set
    the files come from AWS S3.
    """
    endpoint = config.get("S3_PUBLIC_ENDPOINT_URL") or config.get("S3_ENDPOINT_URL")
    if not endpoint:
        region = config.get("S3_REGION") or "us-east-1"
        return ["https://s3.amazonaws.com", f"https://s3.{region}.amazonaws.com"]
    parts = urlsplit(endpoint)
    return [f"{parts.scheme}://{parts.netloc}"] if parts.scheme and parts.netloc else []


def content_security_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    """The app's policy: the base policy plus the storage origin, for the hospital's bill viewer.

    Only images and frames may load from storage (a bill is shown as a picture or a PDF); scripts,
    styles and connections may not.
    """
    policy = {
        name: list(value) if isinstance(value, list) else value
        for name, value in CONTENT_SECURITY_POLICY.items()
    }
    storage = storage_origins(config)
    policy["img-src"] = [*policy["img-src"], *storage]
    policy["frame-src"] = [*policy["frame-src"], *storage]
    return policy


# Swagger UI loads its scripts and styles from a CDN and starts with an inline script.
# Only the documentation page gets this looser policy.
_SWAGGER_CDN = "https://cdn.jsdelivr.net"
DOCS_CONTENT_SECURITY_POLICY: dict[str, Any] = {
    "default-src": "'self'",
    "script-src": ["'self'", "'unsafe-inline'", _SWAGGER_CDN],
    "style-src": ["'self'", "'unsafe-inline'", _SWAGGER_CDN],
    "img-src": ["'self'", "data:", _SWAGGER_CDN],
    "connect-src": "'self'",
    "frame-ancestors": "'none'",
    "object-src": "'none'",
}
_DOCS_ENDPOINT = "api-docs.openapi_swagger_ui"
# The platform's health probe calls the service directly over plain HTTP, with no forwarded scheme.
# A redirect there could be read as unhealthy, so only this route skips the HTTPS redirect.
_HEALTH_ENDPOINT = "health.Health"

CSRF_HEADER = "X-CSRFToken"


def apply_proxy_fix(app: Flask) -> None:
    """Trust ``PROXY_FIX_COUNT`` proxies for the client address, scheme and host.

    Render terminates TLS in front of the app, so rate limits and the HTTPS checks need the
    forwarded values. Zero (the default) trusts nothing, because a client could forge the headers.
    """
    count = app.config.get("PROXY_FIX_COUNT", 0)
    if count > 0:
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=count, x_proto=count, x_host=count
        )


def init_security_headers(app: Flask, talisman: Talisman) -> None:
    """HSTS and the HTTPS redirect in production, plus the headers every environment gets."""
    production = app.config["APP_ENV"] == "production"
    talisman.init_app(
        app,
        force_https=production,
        strict_transport_security=production,
        strict_transport_security_max_age=31_536_000,
        strict_transport_security_include_subdomains=True,
        content_security_policy=content_security_policy(app.config),
        content_security_policy_nonce_in=[],
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
        permissions_policy={"geolocation": "()", "camera": "()", "microphone": "()"},
        session_cookie_secure=app.config["SESSION_COOKIE_SECURE"],
        session_cookie_http_only=True,
        session_cookie_samesite="Lax",
    )
    _relax_policy_for_docs(app)


def _set_view_options(app: Flask, endpoint: str, **options: Any) -> None:
    """Give one view its own Talisman options (the view function must be a plain function)."""
    view = app.view_functions.get(endpoint)
    if view is None:
        return

    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        return view(*args, **kwargs)

    wrapped.talisman_view_options = options  # type: ignore[attr-defined]
    app.view_functions[endpoint] = wrapped


def _relax_policy_for_docs(app: Flask) -> None:
    _set_view_options(
        app,
        _DOCS_ENDPOINT,
        content_security_policy=DOCS_CONTENT_SECURITY_POLICY,
        content_security_policy_nonce_in=[],
    )


def exempt_health_from_https_redirect(app: Flask) -> None:
    """Call after the routes exist: the health probe must not be redirected to HTTPS."""
    _set_view_options(app, _HEALTH_ENDPOINT, force_https=False)


def init_cors(app: Flask) -> None:
    """Allow the configured browser origins (the local Vite server) to call ``/api``."""
    origins = app.config["CORS_ORIGINS"]
    if not origins:
        return
    CORS(
        app,
        resources={r"/api/*": {"origins": origins}},
        supports_credentials=True,
        always_send=False,
        allow_headers=["Content-Type", CSRF_HEADER, "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )


def declare_security_schemes(api: Api) -> None:
    """Describe the session cookie and the CSRF header in the OpenAPI document."""
    api.spec.components.security_scheme(
        "sessionCookie", {"type": "apiKey", "in": "cookie", "name": "td_session"}
    )
    api.spec.components.security_scheme(
        "csrfToken", {"type": "apiKey", "in": "header", "name": CSRF_HEADER}
    )
