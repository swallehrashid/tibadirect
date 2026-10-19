"""The initial migration: upgrade, downgrade, upgrade, and agreement with the models.

These tests build their own throwaway database so the shared test database is never downgraded.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import flask_migrate
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models.enums import ALL_ENUMS

MIGRATIONS_DIR = str(Path(__file__).resolve().parents[1] / "migrations")
SCRATCH_DB = "tibadirect_migrations_test"
EXPECTED_TABLES = 22


def _admin_engine(test_url: str):
    admin_url = make_url(test_url).set(database="postgres")
    return create_engine(admin_url, isolation_level="AUTOCOMMIT")


@pytest.fixture
def scratch_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Flask]:
    base_url = make_url(
        os.environ.get("TEST_DATABASE_URL")
        or "postgresql+psycopg://tiba:tiba@localhost:5432/tibadirect_test"
    )
    scratch_url = base_url.set(database=SCRATCH_DB).render_as_string(hide_password=False)
    admin = _admin_engine(base_url.render_as_string(hide_password=False))
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


def _tables(application: Flask) -> set[str]:
    with application.app_context():
        rows = db.session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        return {row[0] for row in rows}


def _count(application: Flask, sql: str) -> int:
    with application.app_context():
        return db.session.execute(text(sql)).scalar_one()


def test_upgrade_creates_the_whole_schema(scratch_app: Flask) -> None:
    with scratch_app.app_context():
        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
    tables = _tables(scratch_app)
    assert "alembic_version" in tables
    assert len(tables - {"alembic_version"}) == EXPECTED_TABLES
    assert tables - {"alembic_version"} == set(db.metadata.tables)


def test_upgrade_downgrade_upgrade_is_clean(scratch_app: Flask) -> None:
    with scratch_app.app_context():
        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
        flask_migrate.downgrade(directory=MIGRATIONS_DIR, revision="base")

    assert _tables(scratch_app) == {"alembic_version"}
    enum_types = "SELECT count(*) FROM pg_type WHERE typtype = 'e'"
    assert _count(scratch_app, enum_types) == 0
    assert _count(scratch_app, "SELECT count(*) FROM pg_extension WHERE extname = 'citext'") == 0
    functions = "SELECT count(*) FROM pg_proc WHERE proname = 'ledger_entries_append_only'"
    assert _count(scratch_app, functions) == 0

    with scratch_app.app_context():
        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
    assert len(_tables(scratch_app) - {"alembic_version"}) == EXPECTED_TABLES
    assert _count(scratch_app, "SELECT count(*) FROM pg_type WHERE typtype = 'e'") == len(ALL_ENUMS)


def test_migrated_schema_matches_the_models(scratch_app: Flask) -> None:
    """If a model changes without a migration, autogenerate would find a difference."""
    with scratch_app.app_context():
        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, db.metadata)
    assert differences == []


def test_the_revision_is_recorded(scratch_app: Flask) -> None:
    with scratch_app.app_context():
        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
    assert (
        _count(scratch_app, "SELECT count(*) FROM alembic_version WHERE version_num = '0001'") == 1
    )
