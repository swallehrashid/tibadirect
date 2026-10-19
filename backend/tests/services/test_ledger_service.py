import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from app.models.enums import LedgerAccount, LedgerEntryType
from app.models.ledger import GENESIS_HASH
from app.services import ledger_service
from app.services.ledger_service import NewEntry
from tests.factories import CaseFactory

STAMP = datetime(2026, 10, 19, 12, 30, 15, 123456, tzinfo=UTC)


def _new(case_id, amount=2500, account=LedgerAccount.CASE_FUNDS, currency="USD") -> NewEntry:
    return NewEntry(
        case_id=case_id,
        entry_type=LedgerEntryType.DONATION_CAPTURED,
        account=account,
        amount_minor=amount,
        currency=currency,
        ref_type="donation",
        ref_id=uuid.uuid4(),
    )


def test_canonical_payload_is_sorted_compact_json() -> None:
    case_id, ref_id = uuid.UUID(int=1), uuid.UUID(int=2)
    payload = ledger_service.canonical_payload(
        seq=7,
        case_id=case_id,
        entry_type=LedgerEntryType.PAYPAL_FEE,
        account=LedgerAccount.PAYPAL_FEES,
        amount_minor=-75,
        currency="USD",
        ref_type="donation",
        ref_id=ref_id,
        created_at=STAMP,
        prev_hash=GENESIS_HASH,
    )
    expected = (
        '{"account":"paypal_fees","amount_minor":-75,'
        f'"case_id":"{case_id}","created_at_iso":"2026-10-19T12:30:15.123456+00:00",'
        '"currency":"USD","entry_type":"paypal_fee",'
        f'"prev_hash":"{GENESIS_HASH}","ref_id":"{ref_id}","ref_type":"donation","seq":7}}'
    )
    assert payload == expected
    assert json.loads(payload)["seq"] == 7
    assert " " not in payload


def test_canonical_payload_converts_to_utc_and_keeps_microseconds() -> None:
    from datetime import timedelta, timezone

    eat = timezone(timedelta(hours=3))
    local = datetime(2026, 10, 19, 15, 30, 15, 0, tzinfo=eat)
    kwargs = {
        "seq": 1,
        "case_id": None,
        "entry_type": LedgerEntryType.ADJUSTMENT,
        "account": LedgerAccount.CASE_FUNDS,
        "amount_minor": 1,
        "currency": "USD",
        "ref_type": "x",
        "ref_id": uuid.UUID(int=3),
        "prev_hash": GENESIS_HASH,
    }
    payload = ledger_service.canonical_payload(created_at=local, **kwargs)
    assert '"created_at_iso":"2026-10-19T12:30:15.000000+00:00"' in payload
    assert '"case_id":null' in payload


def test_first_entry_links_to_the_genesis_hash(db_session) -> None:
    [entry] = ledger_service.append(db_session, [_new(None)], created_at=STAMP)
    assert entry.prev_hash == GENESIS_HASH
    assert len(entry.hash) == 64


def test_entries_chain_to_each_other_in_sequence(db_session) -> None:
    case = CaseFactory()
    db_session.flush()
    first = ledger_service.append(db_session, [_new(case.id), _new(case.id, 100)], created_at=STAMP)
    [later] = ledger_service.append(db_session, [_new(case.id, -50)], created_at=STAMP)
    assert first[1].prev_hash == first[0].hash
    assert later.prev_hash == first[1].hash
    assert first[0].seq < first[1].seq < later.seq


def test_stored_hash_can_be_recomputed_after_a_round_trip(db_session) -> None:
    [entry] = ledger_service.append(db_session, [_new(None)])
    db_session.expire_all()
    assert ledger_service.entry_hash(entry) == entry.hash


def test_appending_nothing_does_nothing(db_session) -> None:
    assert ledger_service.append(db_session, []) == []


def test_an_intact_chain_verifies(db_session) -> None:
    case = CaseFactory()
    db_session.flush()
    ledger_service.append(db_session, [_new(case.id), _new(case.id, -10)])
    status = ledger_service.verify_chain(db_session)
    assert status.valid
    assert status.entries_checked == 2
    assert status.first_bad_seq is None


def test_an_empty_ledger_is_valid_with_the_genesis_hash(db_session) -> None:
    status = ledger_service.verify_chain(db_session)
    assert (status.valid, status.entries_checked, status.last_hash) == (True, 0, GENESIS_HASH)


def test_verify_counts_only_the_requested_case(db_session) -> None:
    first, second = CaseFactory(), CaseFactory()
    db_session.flush()
    ledger_service.append(db_session, [_new(first.id), _new(first.id), _new(second.id)])
    assert ledger_service.verify_chain(db_session, first.id).entries_checked == 2
    assert ledger_service.verify_chain(db_session, second.id).entries_checked == 1


def _tamper(db_session, sql: str, **params) -> None:
    """Change a row the way an attacker with database access would: past the append-only trigger."""
    db_session.execute(
        text("ALTER TABLE ledger_entries DISABLE TRIGGER trg_ledger_entries_append_only")
    )
    db_session.execute(text(sql), params)
    db_session.execute(
        text("ALTER TABLE ledger_entries ENABLE TRIGGER trg_ledger_entries_append_only")
    )
    db_session.expire_all()


def test_changing_an_amount_breaks_verification_at_that_entry(db_session) -> None:
    entries = ledger_service.append(db_session, [_new(None, 100), _new(None, 200), _new(None, 300)])
    _tamper(
        db_session, "UPDATE ledger_entries SET amount_minor = 999 WHERE id = :id", id=entries[1].id
    )
    status = ledger_service.verify_chain(db_session)
    assert status.valid is False
    assert status.first_bad_seq == entries[1].seq
    assert status.entries_checked == 1


def test_removing_a_middle_entry_breaks_the_chain(db_session) -> None:
    entries = ledger_service.append(db_session, [_new(None, 100), _new(None, 200), _new(None, 300)])
    _tamper(db_session, "DELETE FROM ledger_entries WHERE id = :id", id=entries[1].id)
    status = ledger_service.verify_chain(db_session)
    assert status.valid is False
    assert status.first_bad_seq == entries[2].seq


def test_rewriting_a_hash_to_match_still_breaks_the_next_link(db_session) -> None:
    entries = ledger_service.append(db_session, [_new(None, 100), _new(None, 200)])
    forged = ledger_service.entry_hash(entries[0])
    _tamper(
        db_session,
        "UPDATE ledger_entries SET prev_hash = :p WHERE id = :id",
        p="f" * 64,
        id=entries[0].id,
    )
    assert forged != "f" * 64
    status = ledger_service.verify_chain(db_session)
    assert status.valid is False
    assert status.first_bad_seq == entries[0].seq


def test_the_chain_continues_after_a_truncate(db_session) -> None:
    ledger_service.append(db_session, [_new(None)])
    db_session.execute(text("TRUNCATE ledger_entries RESTART IDENTITY"))
    [entry] = ledger_service.append(db_session, [_new(None)])
    assert (entry.seq, entry.prev_hash) == (1, GENESIS_HASH)
    assert ledger_service.verify_chain(db_session).valid


def test_appends_take_the_advisory_lock(db_session) -> None:
    ledger_service.append(db_session, [_new(None)])
    held = db_session.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND objid = :key AND pid = pg_backend_pid()"
        ),
        {"key": ledger_service.LEDGER_LOCK_KEY},
    ).scalar_one()
    assert held >= 1
