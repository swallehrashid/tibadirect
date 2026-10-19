"""Shared fixtures: the app, a Postgres test database and per-test rolled-back transactions."""

from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import text
from sqlalchemy.orm import scoped_session

from app import create_app
from app.extensions import db
from tests import support


@pytest.fixture(scope="session")
def app() -> Flask:
    application = create_app("test")
    support.register(application)
    return application


@pytest.fixture(scope="session", autouse=True)
def _schema(app: Flask) -> Iterator[None]:
    """Build the schema once per run. F10 replaces create_all with the Alembic migration."""
    with app.app_context():
        db.session.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        db.session.commit()
        db.drop_all()
        db.create_all()
    yield
    with app.app_context():
        db.session.remove()
        db.drop_all()


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
