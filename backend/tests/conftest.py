"""Shared fixtures: the app, a Postgres test database and per-test rolled-back transactions."""

from collections.abc import Callable, Iterator
from pathlib import Path

import flask_migrate
import pytest
from flask import Flask
from flask.testing import FlaskClient
from moto import mock_aws
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import scoped_session

from app import create_app
from app.extensions import db, limiter
from app.models.identity import User
from app.services import mail_service
from tests import support
from tests.browser import Browser

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture(scope="session")
def app() -> Flask:
    application = create_app("test")
    support.register(application)
    return application


def reset_schema() -> None:
    """Empty the test database. Refuses to touch anything not named *_test."""
    name = db.session.execute(text("SELECT current_database()")).scalar_one()
    assert name.endswith("_test"), f"refusing to reset {name}"
    db.session.execute(text("DROP SCHEMA public CASCADE"))
    db.session.execute(text("CREATE SCHEMA public"))
    db.session.commit()


@pytest.fixture(scope="session", autouse=True)
def _schema(app: Flask) -> Iterator[None]:
    """Build the schema once per run by applying the real Alembic migrations."""
    with app.app_context():
        reset_schema()
        flask_migrate.upgrade(directory=str(MIGRATIONS_DIR))
    yield
    with app.app_context():
        db.session.remove()
        reset_schema()


@pytest.fixture
def db_session(app: Flask) -> Iterator[scoped_session]:
    """Run each test inside a transaction that is rolled back afterwards.

    The session joins an outer connection transaction through savepoints, so code under test
    can commit and roll back freely without leaving rows behind.
    """
    with app.app_context():
        engines = db.engines
        engine = engines[None]
        connection = engine.connect()
        outer = connection.begin()
        db.session.remove()
        db.session.configure(join_transaction_mode="create_savepoint")
        engines[None] = connection  # type: ignore[assignment]
        try:
            yield db.session
        finally:
            db.session.remove()
            outer.rollback()
            connection.close()
            engines[None] = engine


@pytest.fixture
def client(app: Flask, db_session: scoped_session) -> FlaskClient:
    return app.test_client()


SCRATCH_DB = "tibadirect_scratch_test"


@pytest.fixture
def scratch_app(app: Flask, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    """An app bound to a brand-new empty database that is dropped afterwards.

    For tests that commit for real (CLI commands) or that run migrations up and down, so the
    shared test database is never touched.
    """
    base_url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    scratch_url = base_url.set(database=SCRATCH_DB).render_as_string(hide_password=False)
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{SCRATCH_DB}"'))

    monkeypatch.setenv("TEST_DATABASE_URL", scratch_url)
    application = create_app("test")
    try:
        yield application
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def migrated_scratch_app(scratch_app: Flask) -> Flask:
    with scratch_app.app_context():
        flask_migrate.upgrade(directory=str(MIGRATIONS_DIR))
    return scratch_app


@pytest.fixture
def csrf_on(app: Flask) -> Iterator[None]:
    """Switch CSRF protection on for the shared app, as it is in production."""
    app.config["WTF_CSRF_ENABLED"] = True
    yield
    app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture(autouse=True)
def _fresh_limits_and_outbox() -> Iterator[None]:
    limiter.reset()
    mail_service.outbox.clear()
    yield
    limiter.reset()


@pytest.fixture
def browser(app: Flask, db_session: scoped_session, csrf_on: None) -> Browser:
    return Browser(app.test_client())


@pytest.fixture
def browser_for(app: Flask, db_session: scoped_session, csrf_on: None) -> Callable[..., Browser]:
    """Make a browser already signed in as ``user`` (session set directly, then a fresh token)."""

    def make(user: User, remote_addr: str = "198.51.100.10") -> Browser:
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
        browser = Browser(client, remote_addr)
        browser.refresh_csrf()
        return browser

    return make


@pytest.fixture
def redis_clean(app: Flask) -> Iterator[None]:
    """Empty the test Redis database (number 15) before and after a test that uses queues.

    No application context stays open during the test, so it never swaps the database session
    that other fixtures and factories are using.
    """

    def flush() -> None:
        with app.app_context():
            from app.jobs.queue import get_connection

            connection = get_connection()
            assert connection.connection_pool.connection_kwargs["db"] == 15, "refusing to flush"
            connection.flushdb()

    flush()
    yield
    flush()


@pytest.fixture
def async_jobs(app: Flask, redis_clean: None) -> Iterator[None]:
    """Jobs wait on the queue for a worker, as in production, instead of running inline."""
    app.config["JOBS_ASYNC"] = True
    yield
    app.config["JOBS_ASYNC"] = False


@pytest.fixture(scope="session", autouse=True)
def _no_real_object_storage() -> Iterator[None]:
    """Every S3 call in the test run goes to moto's in-memory service, never to a real server."""
    with mock_aws():
        yield


@pytest.fixture
def s3(app: Flask) -> Iterator[None]:
    """An empty private bucket for the test, emptied and removed afterwards.

    No application context stays open during the test: Flask's CLI reuses an open context, which
    would point commands run against a scratch app at this app's database.
    """
    from app.services import storage_service

    with app.app_context():
        storage_service.ensure_bucket()
    yield
    with app.app_context():
        client = storage_service._client()
        bucket = app.config["S3_BUCKET"]
        for key in list(storage_service._keys_under("")):
            client.delete_object(Bucket=bucket, Key=key)
        client.delete_bucket(Bucket=bucket)
