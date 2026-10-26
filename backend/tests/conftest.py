"""Shared fixtures: the app, a Postgres test database and per-test rolled-back transactions."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import flask_migrate
import pytest
from alembic.script import ScriptDirectory
from dotenv import find_dotenv, load_dotenv
from flask import Flask
from flask.testing import FlaskClient
from moto import mock_aws
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import scoped_session
from sqlalchemy.pool import NullPool

from app import create_app
from app.config import TestConfig, with_redis_database
from app.extensions import db, limiter
from app.models.identity import User
from app.payments.paypal_client import get_client
from app.payments.paypal_mock import MockPayPal
from app.services import mail_service
from tests import support
from tests.browser import Browser

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Under pytest-xdist (`pytest -n auto`) each worker is a separate process named gw0, gw1, ... and
# gets its own test database and Redis number, so workers never rebuild or flush each other's.
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_REDIS_DB = 15 - int(WORKER.removeprefix("gw") or 0)


def _give_this_worker_its_own_database_and_redis() -> None:
    if not WORKER:
        return
    assert TEST_REDIS_DB >= 1, "at most 15 workers: Redis database 0 is the development one"
    load_dotenv(find_dotenv(usecwd=True), override=False)  # as create_app does
    shared = TestConfig()
    url = make_url(shared.SQLALCHEMY_DATABASE_URI)
    name = url.database.removesuffix("_test") + f"_{WORKER}_test"
    os.environ["TEST_DATABASE_URL"] = url.set(database=name).render_as_string(hide_password=False)
    os.environ["TEST_REDIS_URL"] = with_redis_database(shared.REDIS_URL, TEST_REDIS_DB)


_give_this_worker_its_own_database_and_redis()


def _admin_engine(url: str) -> Engine:
    return create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")


def _ensure_database(url: str) -> None:
    """Create a test database if it is missing.

    Its commits do not wait for the disk: a crash could lose only the last moments of test data,
    and on a slow disk the waiting is most of a test run.
    """
    name = make_url(url).database
    assert (name or "").endswith("_test"), f"refusing to touch {name}"
    admin = _admin_engine(url)
    with admin.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        connection.execute(text(f'ALTER DATABASE "{name}" SET synchronous_commit = off'))
    admin.dispose()


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
    """Build the schema once per run by applying the real Alembic migrations.

    It is emptied at the start of the next run rather than at the end of this one: dropping it
    here only made every worker queue on the disk while the run was finishing.
    """
    _ensure_database(app.config["SQLALCHEMY_DATABASE_URI"])
    with app.app_context():
        reset_schema()
        flask_migrate.upgrade(directory=str(MIGRATIONS_DIR))
    yield
    with app.app_context():
        db.session.remove()


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


SCRATCH_DB = f"tibadirect_scratch_{WORKER}_test" if WORKER else "tibadirect_scratch_test"


@pytest.fixture(scope="session")
def _scratch_url(app: Flask) -> str:
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"]).set(database=SCRATCH_DB)
    rendered = url.render_as_string(hide_password=False)
    _ensure_database(rendered)
    # Emptied now, so a schema left by an earlier run (perhaps from an edited migration) is never
    # mistaken for the current one; it is kept afterwards for the next run to reuse.
    _empty_database(rendered)
    return rendered


def _terminate_other_connections(url: str) -> None:
    """Disconnect anything a previous test left connected to the scratch database."""
    engine = create_engine(url, poolclass=NullPool)
    with engine.begin() as connection:
        name = connection.execute(text("SELECT current_database()")).scalar_one()
        assert name == SCRATCH_DB, f"refusing to touch {name}"
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
    engine.dispose()


def _empty_database(url: str) -> None:
    """Drop everything in the scratch database."""
    _terminate_other_connections(url)
    engine = create_engine(url, poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@contextmanager
def _app_on(url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    application = create_app("test")
    try:
        yield application
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()


def _clear_rows_if_migrated(url: str) -> bool:
    """Empty every table and restart the sequences, if the schema is the latest one.

    Running the migrations takes seconds on a slow disk; clearing the rows gives the same
    database. Anything else (an empty database, one left half way down) returns False.
    """
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            tables = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            )
            if tables != {*db.metadata.tables, "alembic_version"}:
                return False
            versions = connection.execute(text("SELECT version_num FROM alembic_version")).all()
            if [row[0] for row in versions] != [head]:
                return False
            # DELETE, not TRUNCATE: TRUNCATE makes new files for every table, which is slow on a
            # slow disk. Pausing triggers and foreign keys lets the append-only ledger be emptied.
            connection.execute(text("SET LOCAL session_replication_role = replica"))
            for name in db.metadata.tables:
                connection.execute(text(f'DELETE FROM "{name}"'))
            sequences = connection.execute(
                text("SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'")
            ).scalars()
            for sequence in list(sequences):
                connection.execute(text(f'ALTER SEQUENCE "{sequence}" RESTART'))
            return True
    finally:
        engine.dispose()


@pytest.fixture
def scratch_app(_scratch_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    """An app bound to an empty database of its own.

    For tests that commit for real (CLI commands) or that run migrations up and down, so the
    shared test database is never touched. The database is made once and emptied before each
    test: dropping a database makes Postgres flush everything to disk, which took seconds per
    test and held up every other test running at the time.
    """
    _empty_database(_scratch_url)
    with _app_on(_scratch_url, monkeypatch) as application:
        yield application


@pytest.fixture
def migrated_scratch_app(_scratch_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    """The scratch database with the latest schema and no rows."""
    _terminate_other_connections(_scratch_url)
    migrated = _clear_rows_if_migrated(_scratch_url)
    if not migrated:
        _empty_database(_scratch_url)
    with _app_on(_scratch_url, monkeypatch) as application:
        if not migrated:
            with application.app_context():
                flask_migrate.upgrade(directory=str(MIGRATIONS_DIR))
        yield application


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
    """Empty the test Redis database (number 15, or this worker's) around a test that uses queues.

    No application context stays open during the test, so it never swaps the database session
    that other fixtures and factories are using.
    """

    def flush() -> None:
        with app.app_context():
            from app.jobs.queue import get_connection

            connection = get_connection()
            assert connection.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB, "refusing"
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


@pytest.fixture(autouse=True)
def _fresh_paypal_gateway(app: Flask) -> Iterator[None]:
    """Each test builds its own PayPal client, so the fake's orders never leak between tests."""
    app.extensions.pop("paypal_gateway", None)
    yield
    app.extensions.pop("paypal_gateway", None)


@pytest.fixture
def paypal(app: Flask) -> MockPayPal:
    """The offline PayPal fake the app is using, to approve orders and inject failures."""
    with app.app_context():
        gateway = get_client()
    assert isinstance(gateway, MockPayPal)
    return gateway


@pytest.fixture
def manual_settlement(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Settlement jobs are recorded instead of run, so a test drives each step itself.

    Tests run the job queue inline, so without this a donation would create, send and follow a
    tranche on its own, which is right end to end but hides the steps from a test about one.
    """
    from app.services import settlement_service

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(settlement_service, "_enqueue", lambda func, arg: seen.append((func, arg)))
    return seen


@pytest.fixture
def manual_refunds(monkeypatch: pytest.MonkeyPatch) -> list:
    """Refund jobs are recorded instead of run, so a test decides when PayPal is asked."""
    from app.services import refund_service

    seen: list = []
    monkeypatch.setattr(refund_service, "enqueue_execute", seen.append)
    return seen
