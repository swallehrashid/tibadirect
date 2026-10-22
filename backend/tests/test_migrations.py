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
        _count(scratch_app, "SELECT count(*) FROM alembic_version WHERE version_num = '0003'") == 1
    )


def test_the_invoice_columns_arrive_with_0003_and_leave_with_a_downgrade(
    scratch_app: Flask,
) -> None:
    """Revision 0003 adds the invoice status, amount and payment ID."""

    def present(table: str, column: str) -> int:
        return _count(
            scratch_app,
            "SELECT count(*) FROM information_schema.columns "
            f"WHERE table_name = '{table}' AND column_name = '{column}'",
        )

    wanted = [
        ("confirmations", "invoice_status"),
        ("confirmations", "invoice_usd_minor"),
        ("settlements", "invoice_payment_id"),
    ]
    with scratch_app.app_context():
        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
        assert [present(*pair) for pair in wanted] == [1, 1, 1]

        flask_migrate.downgrade(directory=MIGRATIONS_DIR, revision="0002")
        assert [present(*pair) for pair in wanted] == [0, 0, 0]

        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
        assert [present(*pair) for pair in wanted] == [1, 1, 1]


def test_the_pooled_amount_arrives_with_its_rule_and_leaves_with_a_downgrade(
    scratch_app: Flask,
) -> None:
    """Revision 0002 adds ``donations.pooled_usd_minor`` and widens the allocation rule."""
    column = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'donations' AND column_name = 'pooled_usd_minor'"
    )
    rule = (
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'ck_donations_allocation_within_net'"
    )
    with scratch_app.app_context():
        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
        assert _count(scratch_app, column) == 1
        assert "pooled_usd_minor" in db.session.execute(text(rule)).scalar_one()

        flask_migrate.downgrade(directory=MIGRATIONS_DIR, revision="0001")
        assert _count(scratch_app, column) == 0
        assert "pooled_usd_minor" not in db.session.execute(text(rule)).scalar_one()

        flask_migrate.upgrade(directory=MIGRATIONS_DIR)
        assert _count(scratch_app, column) == 1


def test_a_donation_cannot_hold_more_than_its_net_across_paid_refunded_and_pooled(
    migrated_scratch_app: Flask,
) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    with migrated_scratch_app.app_context():
        from tests.factories import DonationFactory

        DonationFactory(net_usd_minor=1000, allocated_usd_minor=300, refunded_usd_minor=300)
        with pytest.raises(IntegrityError, match="ck_donations_allocation_within_net"):
            DonationFactory(
                net_usd_minor=1000,
                allocated_usd_minor=400,
                refunded_usd_minor=300,
                pooled_usd_minor=400,
            )
