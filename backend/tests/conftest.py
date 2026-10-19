"""Shared fixtures: the app, a Postgres test database and per-test rolled-back transactions."""

from collections.abc import Iterator
from pathlib import Path

import flask_migrate
import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import text
from sqlalchemy.orm import scoped_session

from app import create_app
from app.extensions import db
from tests import support

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
