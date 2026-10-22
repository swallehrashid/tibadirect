"""Appends from many connections at once still form one unbroken chain."""

import threading
import uuid

from flask import Flask
from sqlalchemy import select

from app.extensions import db
from app.models.enums import LedgerAccount, LedgerEntryType
from app.models.ledger import GENESIS_HASH, LedgerEntry
from app.services import ledger_service
from app.services.ledger_service import NewEntry

THREADS = 8
APPENDS_PER_THREAD = 6


def _entry() -> NewEntry:
    return NewEntry(
        None,
        LedgerEntryType.ADJUSTMENT,
        LedgerAccount.CASE_FUNDS,
        100,
        "USD",
        "adjustment",
        uuid.uuid4(),
    )


def test_concurrent_appends_stay_in_one_ordered_unbroken_chain(migrated_scratch_app: Flask) -> None:
    errors: list[BaseException] = []
    start = threading.Barrier(THREADS)

    def worker() -> None:
        try:
            with migrated_scratch_app.app_context():
                start.wait(timeout=10)
                for _ in range(APPENDS_PER_THREAD):
                    ledger_service.append(db.session, [_entry(), _entry()])
                    db.session.commit()
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread below
            errors.append(exc)
        finally:
            with migrated_scratch_app.app_context():
                db.session.remove()

    threads = [threading.Thread(target=worker) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors

    with migrated_scratch_app.app_context():
        entries = db.session.execute(select(LedgerEntry).order_by(LedgerEntry.seq)).scalars().all()
        expected = THREADS * APPENDS_PER_THREAD * 2
        assert len(entries) == expected
        assert [e.seq for e in entries] == list(range(1, expected + 1))
        assert entries[0].prev_hash == GENESIS_HASH
        for before, after in zip(entries, entries[1:], strict=False):
            assert after.prev_hash == before.hash
        assert len({e.hash for e in entries}) == expected
        status = ledger_service.verify_chain(db.session)
        assert status.valid
        assert status.entries_checked == expected
