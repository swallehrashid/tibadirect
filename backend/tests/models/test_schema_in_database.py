"""Checks against the migrated database itself: enums, extension, trigger and the ledger."""

import uuid

import pytest
from sqlalchemy import text

from app.models.enums import ALL_ENUMS, db_enum_name
from tests.factories import LedgerEntryFactory
from tests.models.helpers import assert_db_rejects


def test_citext_extension_is_installed(db_session) -> None:
    assert (
        db_session.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'citext'")
        ).scalar_one()
        == 1
    )


@pytest.mark.parametrize("enum_cls", ALL_ENUMS, ids=lambda cls: db_enum_name(cls))
def test_database_enum_labels_match_the_python_enum_in_order(db_session, enum_cls) -> None:
    labels = (
        db_session.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :name ORDER BY e.enumsortorder"
            ),
            {"name": db_enum_name(enum_cls)},
        )
        .scalars()
        .all()
    )
    assert labels == [member.value for member in enum_cls]


def test_the_database_has_exactly_the_designed_enum_types(db_session) -> None:
    names = set(
        db_session.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'")).scalars()
    )
    assert names == {db_enum_name(cls) for cls in ALL_ENUMS}


def test_ledger_rows_can_be_inserted(db_session) -> None:
    entry = LedgerEntryFactory()
    db_session.flush()
    assert entry.seq is not None


def test_updating_a_ledger_row_is_rejected(db_session) -> None:
    entry = LedgerEntryFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(
            text("UPDATE ledger_entries SET amount_minor = 1 WHERE id = :id"), {"id": entry.id}
        ),
    )
    assert "append-only" in str(error)


def test_updating_through_the_orm_is_rejected_too(db_session) -> None:
    entry = LedgerEntryFactory()
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: setattr(entry, "memo", "edited"))
    assert "append-only" in str(error)


def test_deleting_a_ledger_row_is_rejected(db_session) -> None:
    entry = LedgerEntryFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(
            text("DELETE FROM ledger_entries WHERE id = :id"), {"id": entry.id}
        ),
    )
    assert "append-only" in str(error)


def test_deleting_every_row_is_rejected_too(db_session) -> None:
    LedgerEntryFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session, lambda: db_session.execute(text("DELETE FROM ledger_entries"))
    )
    assert "append-only" in str(error)


def test_truncate_stays_allowed_for_the_demo_reset(db_session) -> None:
    LedgerEntryFactory()
    LedgerEntryFactory()
    db_session.flush()
    db_session.execute(text("TRUNCATE ledger_entries RESTART IDENTITY"))
    assert db_session.execute(text("SELECT count(*) FROM ledger_entries")).scalar_one() == 0
    restarted = LedgerEntryFactory()
    db_session.flush()
    assert restarted.seq == 1


def test_the_trigger_is_attached_to_the_ledger_table(db_session) -> None:
    row = db_session.execute(
        text(
            "SELECT tgname, tgenabled FROM pg_trigger "
            "WHERE tgrelid = 'ledger_entries'::regclass AND NOT tgisinternal"
        )
    ).one()
    assert tuple(row) == ("trg_ledger_entries_append_only", "O")


def test_other_tables_have_no_append_only_trigger(db_session) -> None:
    names = set(
        db_session.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")).scalars()
    )
    assert names == {"trg_ledger_entries_append_only"}


def test_ids_are_uuids_generated_by_the_application(db_session) -> None:
    entry = LedgerEntryFactory()
    db_session.flush()
    assert isinstance(entry.id, uuid.UUID)
    assert entry.id.version == 4
