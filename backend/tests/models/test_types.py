import pytest
from sqlalchemy import Column, MetaData, Table, insert, select, text

from app.models.types import EncryptedBytes

probe = Table("probe_encrypted", MetaData(), Column("secret", EncryptedBytes))


@pytest.fixture
def probe_table(db_session):
    probe.create(db_session.connection())
    return probe


def _raw(db_session) -> list[bytes]:
    rows = db_session.execute(text("SELECT secret FROM probe_encrypted")).all()
    return [bytes(row[0]) for row in rows]


def test_database_holds_ciphertext_and_python_sees_plaintext(db_session, probe_table) -> None:
    db_session.execute(insert(probe_table).values(secret=b"IP/2026/00123"))
    (stored,) = _raw(db_session)
    assert b"IP/2026/00123" not in stored
    assert db_session.execute(select(probe_table.c.secret)).scalar_one() == b"IP/2026/00123"


def test_strings_are_stored_as_utf8(db_session, probe_table) -> None:
    db_session.execute(insert(probe_table).values(secret="wanjiru@example.test"))
    assert db_session.execute(select(probe_table.c.secret)).scalar_one() == b"wanjiru@example.test"


def test_null_stays_null(db_session, probe_table) -> None:
    db_session.execute(insert(probe_table).values(secret=None))
    assert _raw_is_null(db_session)


def _raw_is_null(db_session) -> bool:
    return db_session.execute(text("SELECT secret IS NULL FROM probe_encrypted")).scalar_one()


def test_same_value_encrypts_differently_each_time(db_session, probe_table) -> None:
    db_session.execute(insert(probe_table), [{"secret": b"same"}, {"secret": b"same"}])
    first, second = _raw(db_session)
    assert first != second
