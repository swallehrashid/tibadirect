"""Serving the built web app from Flask: caching, fallbacks and what must never be served."""

import base64
import os
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.exceptions import HTTPException

from app.errors import register_error_handlers
from app.request_id import init_request_id
from app.spa import PLACEHOLDER_DIR, dist_dir, init_spa


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>the app</html>")
    (tmp_path / "assets" / "app.3f9a1c.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "robots.txt").write_text("User-agent: *")
    return tmp_path


def _app(dist_path: Path | None, *, serve: bool = True) -> Flask:
    app = Flask("spa-test")
    app.config.update(SERVE_SPA=serve, FRONTEND_DIST_DIR=str(dist_path) if dist_path else "")
    init_request_id(app)
    register_error_handlers(app)
    # The real app gets this from flask-smorest; a bare one needs it to answer 404 and 405.
    app.register_error_handler(HTTPException, lambda error: error)

    @app.get("/api/real")
    def real() -> dict:
        return {"ok": True}

    init_spa(app)
    return app


def test_the_home_page_is_the_app_and_is_revalidated(dist: Path) -> None:
    response = _app(dist).test_client().get("/")
    assert response.status_code == 200
    assert b"the app" in response.data
    assert response.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize("path", ["/cases/abc123", "/hospital/queue", "/a/b/c/d", "/login"])
def test_any_app_route_falls_back_to_the_page_so_the_router_can_take_over(
    dist: Path, path: str
) -> None:
    response = _app(dist).test_client().get(path)
    assert response.status_code == 200
    assert b"the app" in response.data


def test_hashed_assets_are_cached_for_a_year(dist: Path) -> None:
    response = _app(dist).test_client().get("/assets/app.3f9a1c.js")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert b"console.log" in response.data


def test_other_files_are_served_but_revalidated(dist: Path) -> None:
    for path, marker in (("/favicon.svg", b"<svg"), ("/robots.txt", b"User-agent")):
        response = _app(dist).test_client().get(path)
        assert response.status_code == 200
        assert marker in response.data
        assert response.headers["Cache-Control"] == "no-cache"


def test_a_missing_asset_falls_back_to_the_page_not_an_error(dist: Path) -> None:
    response = _app(dist).test_client().get("/assets/missing.js")
    assert response.status_code == 200
    assert b"the app" in response.data


@pytest.mark.parametrize("path", ["/.env", "/assets/.hidden", "/.git/config", "/%2e%2e/etc/passwd"])
def test_hidden_files_and_traversal_never_leak(dist: Path, path: str) -> None:
    response = _app(dist).test_client().get(path)
    assert b"SECRET=1" not in response.data
    assert b"root:" not in response.data


def test_traversal_with_dot_dot_segments_is_not_served(dist: Path, tmp_path: Path) -> None:
    outside = dist.parent / "outside-secret.txt"
    outside.write_text("do not serve")
    for path in ("/../outside-secret.txt", "/assets/../../outside-secret.txt"):
        response = _app(dist).test_client().get(path)
        assert b"do not serve" not in response.data


def test_the_api_is_untouched_and_unknown_api_paths_are_json_404s(dist: Path) -> None:
    client = _app(dist).test_client()
    assert client.get("/api/real").get_json() == {"ok": True}
    for path in ("/api/nothing", "/api/nothing/deeper", "/api"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "not_found"


def test_only_get_is_served(dist: Path) -> None:
    assert _app(dist).test_client().post("/cases/abc").status_code == 405


def test_without_the_switch_nothing_is_served(dist: Path) -> None:
    assert _app(dist, serve=False).test_client().get("/").status_code == 404


def test_with_no_build_the_placeholder_stands_in() -> None:
    for configured in (None, Path("/definitely/not/here")):
        directory = dist_dir(str(configured) if configured else None)
        assert directory == PLACEHOLDER_DIR
    response = _app(None).test_client().get("/anything")
    assert response.status_code == 200
    assert b"/api/docs" in response.data


def test_a_build_without_an_index_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    assert dist_dir(str(tmp_path)) == PLACEHOLDER_DIR


def test_production_turns_the_page_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_config

    assert get_config("development").SERVE_SPA is False
    assert get_config("test").SERVE_SPA is False
    monkeypatch.setenv("SECRET_KEY", "a-real-secret-for-this-test-only")
    monkeypatch.setenv("HASH_PEPPER", "a-real-pepper-for-this-test-only")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@localhost/db")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "bucket-user-for-this-test")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "bucket-password-for-this-test")
    assert get_config("production").SERVE_SPA is True
    monkeypatch.setenv("SERVE_SPA", "false")
    assert get_config("production").SERVE_SPA is False


def test_the_placeholder_page_points_at_the_api_docs() -> None:
    page = (PLACEHOLDER_DIR / "index.html").read_text()
    assert "/api/docs" in page
    assert "TibaDirect" in page
