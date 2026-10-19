"""The initial migration: upgrade, downgrade, upgrade, and agreement with the models.

These tests build their own throwaway database so the shared test database is never downgraded.
"""

from pathlib import Path

import flask_migrate
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask import Flask
from sqlalchemy import text

from app.extensions import db
from app.models.enums import ALL_ENUMS

MIGRATIONS_DIR = str(Path(__file__).resolve().parents[1] / "migrations")
EXPECTED_TABLES = 22


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
