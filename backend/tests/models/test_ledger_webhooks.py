import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.enums import LedgerAccount, LedgerEntryType, WebhookStatus
from app.models.ledger import GENESIS_HASH
from tests.factories import CaseFactory, LedgerEntryFactory, WebhookEventFactory
from tests.models.helpers import assert_db_rejects


def test_seq_is_assigned_by_the_database_and_increases(db_session) -> None:
    first, second, third = (LedgerEntryFactory() for _ in range(3))
    db_session.flush()
    assert first.seq < second.seq < third.seq


def test_seq_is_an_identity_column_that_accepts_an_explicit_value(db_session) -> None:
    # The ledger service reads the next value first because the hash covers it.
    next_value = db_session.execute(
        text("SELECT nextval(pg_get_serial_sequence('ledger_entries', 'seq'))")
    ).scalar_one()
    entry = LedgerEntryFactory(seq=next_value)
    db_session.flush()
    assert entry.seq == next_value


def test_ledger_defaults_and_optional_fields(db_session) -> None:
    entry = LedgerEntryFactory(case=None, entry_type=LedgerEntryType.POOL_TRANSFER)
    db_session.flush()
    db_session.refresh(entry)
    assert entry.case_id is None
    assert entry.memo is None
    assert entry.created_at is not None


def test_ledger_enums_are_lowercase_native(db_session) -> None:
    LedgerEntryFactory(
        entry_type=LedgerEntryType.PAYPAL_FEE, account=LedgerAccount.PAYPAL_FEES, amount_minor=-75
    )
    row = db_session.execute(text("SELECT entry_type::text, account::text FROM ledger_entries"))
    assert tuple(row.one()) == ("paypal_fee", "paypal_fees")


def test_entries_are_signed_but_never_zero(db_session) -> None:
    assert LedgerEntryFactory(amount_minor=-500).amount_minor == -500
    assert LedgerEntryFactory(amount_minor=500).amount_minor == 500
    error = assert_db_rejects(db_session, lambda: LedgerEntryFactory(amount_minor=0))
    assert "ck_ledger_entries_amount_non_zero" in str(error)


def test_hash_is_unique(db_session) -> None:
    LedgerEntryFactory(hash="a" * 64)
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: LedgerEntryFactory(hash="a" * 64))
    assert "uq_ledger_entries_hash" in str(error)


def test_two_entries_cannot_claim_the_same_predecessor(db_session) -> None:
    LedgerEntryFactory(prev_hash=GENESIS_HASH)
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: LedgerEntryFactory(prev_hash=GENESIS_HASH))
    assert "uq_ledger_entries_prev_hash" in str(error)


def test_hashes_are_fixed_width(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: LedgerEntryFactory(hash="a" * 65))
    assert isinstance(error, DataError)


def test_genesis_hash_is_64_zeros() -> None:
    assert GENESIS_HASH == "0" * 64


def test_currency_is_three_letters(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: LedgerEntryFactory(currency="USDX"))
    assert isinstance(error, DataError)


def test_memo_is_limited_to_200_characters(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: LedgerEntryFactory(memo="m" * 201))
    assert isinstance(error, DataError)


def test_case_entries_come_back_in_sequence_order(db_session) -> None:
    case = CaseFactory()
    entries = [LedgerEntryFactory(case=case) for _ in range(4)]
    db_session.flush()
    db_session.refresh(case)
    assert [e.seq for e in case.ledger_entries] == sorted(e.seq for e in entries)
    assert all(e.case is case for e in case.ledger_entries)


def test_ledger_lookup_index_by_case_and_seq(db_session) -> None:
    definition = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_ledger_entries_case_seq'")
    ).scalar_one()
    assert "(case_id, seq)" in definition


def test_a_case_with_ledger_entries_cannot_be_deleted(db_session) -> None:
    entry = LedgerEntryFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(text("DELETE FROM cases WHERE id = :id"), {"id": entry.case_id}),
    )
    assert "fk_ledger_entries_case_id_cases" in str(error)


def test_ref_id_must_be_a_uuid(db_session) -> None:
    entry = LedgerEntryFactory(ref_id=uuid.uuid4())
    db_session.flush()
    assert isinstance(entry.ref_id, uuid.UUID)


def test_ledger_repr_shows_no_amounts_or_references(db_session) -> None:
    entry = LedgerEntryFactory(amount_minor=987654, memo="private memo")
    db_session.flush()
    assert "987654" not in repr(entry)
    assert "private" not in repr(entry)


# ---- Webhook events ----


def test_webhook_defaults(db_session) -> None:
    event = WebhookEventFactory()
    db_session.refresh(event)
    assert event.status is WebhookStatus.RECEIVED
    assert event.processed_at is None
    assert event.error is None
    assert event.received_at is not None


def test_webhook_event_id_is_the_idempotency_key(db_session) -> None:
    WebhookEventFactory(paypal_event_id="WH-SAME")
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: WebhookEventFactory(paypal_event_id="WH-SAME"))
    assert "uq_webhook_events_paypal_event_id" in str(error)


def test_webhook_payload_round_trips_and_is_required(db_session) -> None:
    payload = {"id": "WH-9", "resource": {"id": "CAP-9", "amount": {"value": "25.00"}}}
    event = WebhookEventFactory(payload=payload)
    db_session.flush()
    db_session.expire(event)
    assert event.payload == payload
    error = assert_db_rejects(db_session, lambda: WebhookEventFactory(payload=None))
    assert isinstance(error, IntegrityError)


def test_signature_flag_is_required(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: WebhookEventFactory(signature_verified=None))
    assert isinstance(error, IntegrityError)


def test_unverified_events_can_be_stored_as_ignored(db_session) -> None:
    event = WebhookEventFactory(signature_verified=False, status=WebhookStatus.IGNORED)
    assert event.signature_verified is False
    assert event.status is WebhookStatus.IGNORED


def test_webhook_lookup_indexes_exist(db_session) -> None:
    names = set(
        db_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'webhook_events'")
        ).scalars()
    )
    assert {"ix_webhook_events_event_type", "ix_webhook_events_resource_id"} <= names


@pytest.mark.parametrize("length", [61])
def test_event_id_length_is_limited(db_session, length: int) -> None:
    error = assert_db_rejects(db_session, lambda: WebhookEventFactory(paypal_event_id="x" * length))
    assert isinstance(error, DataError)
