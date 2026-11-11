"""The demo shortcuts: sign-in code on screen, no mail server, and bill files kept in Postgres."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from flask import Flask
from sqlalchemy import text

from app.config import ConfigError, get_config
from app.extensions import db
from app.services import database_storage, mail_service, storage_service
from tests.browser import Browser
from tests.test_config import GOOD_PRODUCTION_ENV


@pytest.fixture
def database_storage_on(app: Flask, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    monkeypatch.setitem(app.config, "STORAGE_BACKEND", "database")
    yield app
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS demo_storage_objects"))
    app.extensions.pop("database_storage_ready", None)


def test_the_code_comes_back_only_with_the_shortcut_and_the_demo_banner(
    app: Flask, browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = browser.post("/api/auth/request-code", {"email": "mama@example.test"})
    assert "demo_code" not in response.get_json()

    monkeypatch.setitem(app.config, "DEMO_SHOW_LOGIN_CODE", True)
    monkeypatch.setitem(app.config, "DEMO_BANNER", False)
    response = browser.post("/api/auth/request-code", {"email": "mama@example.test"})
    assert "demo_code" not in response.get_json()

    monkeypatch.setitem(app.config, "DEMO_BANNER", True)
    response = browser.post("/api/auth/request-code", {"email": "mama@example.test"})
    code = response.get_json()["demo_code"]
    verified = browser.post("/api/auth/verify", {"email": "mama@example.test", "code": code})
    assert verified.status_code == 200


def test_mail_off_sends_nothing(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.config, "MAIL_BACKEND", "off")
    with app.test_request_context():
        mail_service.send("login_code", "mama@example.test", code="123456", minutes=10)
    assert mail_service.outbox == []


def test_production_skips_the_bucket_keys_only_for_database_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {**GOOD_PRODUCTION_ENV, "S3_ACCESS_KEY_ID": ""}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match="S3_ACCESS_KEY_ID"):
        get_config("production")
    monkeypatch.setenv("STORAGE_BACKEND", "database")
    assert get_config("production").STORAGE_BACKEND == "database"


def test_files_round_trip_and_are_read_through_a_signed_link(
    database_storage_on: Flask,
) -> None:
    app = database_storage_on
    with app.test_request_context():
        assert storage_service.ensure_bucket() is True
        storage_service.put_object("cases/a/b.pdf", b"%PDF-1", "application/pdf")
        storage_service.put_object("demo/c.png", b"png", "image/png")
        assert storage_service.get_object("cases/a/b.pdf") == b"%PDF-1"
        assert storage_service.object_exists("demo/c.png")
        link = storage_service.signed_url("cases/a/b.pdf")

    response = app.test_client().get(link)
    assert response.status_code == 200
    assert response.data == b"%PDF-1"
    assert response.mimetype == "application/pdf"
    assert response.headers["Cache-Control"] == "private, no-store"

    assert app.test_client().get(link[:-3] + "xyz").status_code == 404
    with app.test_request_context():
        assert storage_service.delete_prefix("demo/") == 1
        storage_service.delete_object("cases/a/b.pdf")
        assert not storage_service.object_exists("cases/a/b.pdf")
        with pytest.raises(storage_service.StorageError):
            storage_service.get_object("cases/a/b.pdf")


def test_an_old_link_stops_working(database_storage_on: Flask) -> None:
    with database_storage_on.test_request_context():
        token = database_storage.signed_url("cases/a/b.pdf").rsplit("/", 1)[1]
        assert database_storage.key_from_token(token, max_age=300) == "cases/a/b.pdf"
        assert database_storage.key_from_token(token, max_age=-1) is None


def test_the_file_route_is_closed_with_s3_storage(app: Flask) -> None:
    assert app.test_client().get("/api/files/anything").status_code == 404


def test_the_demo_blueprint_only_names_settings_the_app_reads_and_keeps_secrets_out() -> None:
    root = Path(__file__).resolve().parents[2]
    text_ = (root / "render.demo.yaml").read_text()
    blueprint = yaml.safe_load(text_)
    source = (root / "backend" / "app" / "config.py").read_text()
    read_by_config = set(re.findall(r'_env\w*\(\s*"([A-Z0-9_]+)"', source))
    read_by_config |= set(re.findall(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"', source))
    [web, redis] = blueprint["services"]
    env = {v["key"]: v for v in web["envVars"]}
    assert set(env) - {"DEMO_RESET_ON_START"} <= read_by_config  # that one is read by the script
    assert (web["plan"], redis["plan"], blueprint["databases"][0]["plan"]) == ("free",) * 3
    assert env["STORAGE_BACKEND"]["value"] == "s3"
    for name in (
        "PAYPAL_CLIENT_SECRET",
        "PAYPAL_CLIENT_ID",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "SMTP_PASSWORD",
        "LLM_API_KEY",
    ):
        assert env[name] == {"key": name, "sync": False}
    # Render keeps the quotes of an inline `sh -c "..."`, so the steps live in a script
    assert web["dockerCommand"] == "sh scripts/demo_start.sh"
    script = (root / "backend" / "scripts" / "demo_start.sh").read_text()
    for command in ("db upgrade", "seed", "reset-demo", "app.jobs.scheduler", "gunicorn"):
        assert command in script
