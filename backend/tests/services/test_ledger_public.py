"""The public face of the ledger: shortened references and what a row may reveal."""

import uuid

import pytest

from app.models.enums import (
    CaseStatus,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    SettlementStatus,
)
from app.services import ledger_service
from app.services.ledger_service import NewEntry
from tests.factories import CaseFactory, DonationFactory, RefundFactory, SettlementFactory


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("SHORT", "SHORT"),
        ("EXACTLY10!", "EXACTLY10!"),
        ("8GH29381XQ47", "8GH2…XQ47"),
        ("CAPTURE1234567890", "CAPT…7890"),
    ],
)
def test_long_references_are_cut_in_the_middle(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert ledger_service.short_reference(value) == expected


def _entry(case, entry_type, ref_type, ref_id, amount=2500):  # type: ignore[no-untyped-def]
    return NewEntry(case.id, entry_type, LedgerAccount.CASE_FUNDS, amount, "USD", ref_type, ref_id)


def test_each_kind_of_entry_gets_the_right_reference(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    donation = DonationFactory(
        case=case,
        status=DonationStatus.CAPTURED,
        net_usd_minor=2400,
        paypal_capture_id="CAPTURE-AAAA-1111",
        donor_display_name="Wanjiru",
        is_anonymous=False,
    )
    settlement = SettlementFactory(
        case=case,
        status=SettlementStatus.SUCCEEDED,
        external_item_id="ITEM-BBBB-2222",
        external_batch_id="BATCH-9",
    )  # fmt: skip
    refund = RefundFactory(
        donation=DonationFactory(case=case, net_usd_minor=2400), reason=RefundReason.EXCESS_POLICY,
        paypal_refund_id="REFUND-CCCC-3333",
    )  # fmt: skip
    pool = _entry(case, LedgerEntryType.POOL_TRANSFER, "donation", donation.id, -100)
    stored = ledger_service.append(
        db_session,
        [
            _entry(case, LedgerEntryType.DONATION_CAPTURED, "donation", donation.id),
            _entry(case, LedgerEntryType.SETTLEMENT_SENT, "settlement", settlement.id, -2000),
            _entry(case, LedgerEntryType.REFUND_ISSUED, "refund", refund.id, -500),
            pool,
            _entry(case, LedgerEntryType.ADJUSTMENT, "adjustment", uuid.uuid4(), 1),
        ],
    )
    rows = ledger_service.public_rows(db_session, stored)
    assert [r["reference"] for r in rows] == [
        "CAPT…1111",
        "ITEM…2222",
        "REFU…3333",
        "CAPT…1111",
        None,
    ]
    assert [r["donor_name"] for r in rows] == ["Wanjiru", None, None, None, None]
    assert [r["amount"] for r in rows] == ["25.00", "-20.00", "-5.00", "-1.00", "0.01"]


def test_a_settlement_without_an_item_id_shows_its_batch(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    settlement = SettlementFactory(
        case=case, external_item_id=None, external_batch_id="PAYOUT-BATCH-77"
    )
    [row] = ledger_service.public_rows(
        db_session,
        ledger_service.append(
            db_session,
            [_entry(case, LedgerEntryType.SETTLEMENT_SENT, "settlement", settlement.id, -1)],
        ),
    )
    assert row["reference"] == "PAYO…H-77"


def test_a_missing_referenced_row_does_not_break_the_page(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    stored = ledger_service.append(
        db_session, [_entry(case, LedgerEntryType.DONATION_CAPTURED, "donation", uuid.uuid4())]
    )
    [row] = ledger_service.public_rows(db_session, stored)
    assert (row["reference"], row["donor_name"]) == (None, None)


def test_no_entries_means_no_queries_and_no_rows(db_session) -> None:  # type: ignore[no-untyped-def]
    assert ledger_service.public_rows(db_session, []) == []


def test_a_row_carries_only_the_public_fields(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    [entry] = ledger_service.append(
        db_session, [_entry(case, LedgerEntryType.ADJUSTMENT, "adjustment", uuid.uuid4())]
    )
    [row] = ledger_service.public_rows(db_session, [entry])
    assert set(row) == {
        "created_at", "entry_type", "account", "amount_minor", "amount", "currency",
        "hash_short", "reference", "donor_name",
    }  # fmt: skip
    assert row["hash_short"] == entry.hash[:12]
