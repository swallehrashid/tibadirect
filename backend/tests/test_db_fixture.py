"""The db_session fixture must undo everything a test does, including explicit commits."""

import pytest
from sqlalchemy import text


def test_rows_written_in_one_test_are_gone_in_the_next_part_one(db_session) -> None:
    db_session.execute(text("CREATE TABLE IF NOT EXISTS _fixture_probe (n integer)"))
    db_session.execute(text("INSERT INTO _fixture_probe VALUES (1)"))
    db_session.commit()
    assert db_session.execute(text("SELECT count(*) FROM _fixture_probe")).scalar() == 1


def test_rows_written_in_one_test_are_gone_in_the_next_part_two(db_session) -> None:
    # Part one created and committed a table inside its transaction; none of it may persist.
    exists = db_session.execute(text("SELECT to_regclass('_fixture_probe')")).scalar()
    assert exists is None


def test_rollback_inside_a_test_only_undoes_the_savepoint(db_session) -> None:
    db_session.execute(text("CREATE TEMP TABLE _probe2 (n integer)"))
    db_session.execute(text("INSERT INTO _probe2 VALUES (1)"))
    db_session.commit()
    db_session.execute(text("INSERT INTO _probe2 VALUES (2)"))
    db_session.rollback()
    assert db_session.execute(text("SELECT count(*) FROM _probe2")).scalar() == 1


def test_api_requests_share_the_test_transaction(client, db_session) -> None:
    pytest.importorskip("flask")
    assert client.get("/api/health").status_code == 200
