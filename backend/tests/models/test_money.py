import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.enums import (
    ActorType,
    DonationStatus,
    ExcessChoice,
    RefundReason,
    RefundStatus,
    SettlementMethod,
    SettlementStatus,
)
from app.models.money import Settlement, SettlementAllocation
from tests.factories import (
    CaseFactory,
    DonationFactory,
    FxRateFactory,
    RefundFactory,
    SettlementFactory,
)
from tests.models.helpers import assert_db_rejects

# ---- FX rates ----


def test_fx_rate_keeps_eight_decimal_places(db_session) -> None:
    rate = FxRateFactory(rate=Decimal("129.12345678"))
    db_session.flush()
    db_session.expire(rate)
    assert rate.rate == Decimal("129.12345678")


@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1.5")])
def test_fx_rate_must_be_positive(db_session, value: Decimal) -> None:
    error = assert_db_rejects(db_session, lambda: FxRateFactory(rate=value))
    assert "ck_fx_rates_rate_positive" in str(error)


def test_fx_rate_overflow_is_refused(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: FxRateFactory(rate=Decimal("99999999999")))
    assert isinstance(error, DataError)


def test_latest_rate_index_is_descending_by_time(db_session) -> None:
    definition = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_fx_rates_pair_fetched_at'")
    ).scalar_one()
    assert "(base, quote, fetched_at DESC)" in definition


def test_latest_rate_query_returns_the_newest(db_session) -> None:
    now = datetime.now(UTC)
    FxRateFactory(rate=Decimal("120"), fetched_at=now - timedelta(hours=2))
    FxRateFactory(rate=Decimal("131"), fetched_at=now)
    FxRateFactory(rate=Decimal("125"), fetched_at=now - timedelta(hours=1))
    db_session.flush()
    latest = db_session.execute(
        text(
            "SELECT rate FROM fx_rates WHERE base='USD' AND quote='KES' "
            "ORDER BY fetched_at DESC LIMIT 1"
        )
    ).scalar_one()
    assert latest == Decimal("131")


# ---- Donations ----


def test_donation_defaults(db_session) -> None:
    donation = DonationFactory()
    db_session.refresh(donation)
    assert donation.status is DonationStatus.CREATED
    assert donation.excess_choice is ExcessChoice.REFUND
    assert donation.is_anonymous is True
    assert donation.paypal_capture_id is None
    assert donation.fx_rate_id is None
    assert (donation.paypal_fee_usd_minor, donation.net_usd_minor) == (0, 0)
    assert (donation.allocated_usd_minor, donation.refunded_usd_minor) == (0, 0)
    assert donation.kes_equivalent_minor == 0
    assert donation.donor_email_enc is None
    assert donation.captured_at is None


def test_receipt_token_is_random_32_url_safe_characters(db_session) -> None:
    first, second = DonationFactory(), DonationFactory()
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", first.receipt_token)
    assert first.receipt_token != second.receipt_token


@pytest.mark.parametrize("amount", [0, 99, 1_000_001])
def test_donation_amount_must_be_between_1_and_10000_dollars(db_session, amount: int) -> None:
    error = assert_db_rejects(db_session, lambda: DonationFactory(amount_usd_minor=amount))
    assert "ck_donations_amount_range" in str(error)


@pytest.mark.parametrize("amount", [100, 1_000_000])
def test_donation_amount_limits_are_accepted(db_session, amount: int) -> None:
    assert DonationFactory(amount_usd_minor=amount).amount_usd_minor == amount


def test_paypal_ids_are_unique_and_capture_may_be_unset(db_session) -> None:
    DonationFactory(paypal_order_id="ORDER-A", paypal_capture_id=None)
    DonationFactory(paypal_order_id="ORDER-B", paypal_capture_id=None)
    DonationFactory(paypal_order_id="ORDER-C", paypal_capture_id="CAP-1")
    db_session.flush()
    dup_order = assert_db_rejects(db_session, lambda: DonationFactory(paypal_order_id="ORDER-A"))
    assert "uq_donations_paypal_order_id" in str(dup_order)


def test_capture_id_is_unique(db_session) -> None:
    DonationFactory(paypal_capture_id="CAP-1")
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: DonationFactory(paypal_capture_id="CAP-1"))
    assert "uq_donations_paypal_capture_id" in str(error)


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("paypal_fee_usd_minor", "fee_non_negative"),
        ("allocated_usd_minor", "allocated_non_negative"),
        ("refunded_usd_minor", "refunded_non_negative"),
    ],
)
def test_money_columns_cannot_go_negative(db_session, column: str, constraint: str) -> None:
    error = assert_db_rejects(db_session, lambda: DonationFactory(**{column: -1}))
    assert f"ck_donations_{constraint}" in str(error)


def test_net_cannot_go_negative(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: DonationFactory(net_usd_minor=-1))
    # A negative net also breaks allocated + refunded <= net; the database may report either.
    assert "ck_donations_net_non_negative" in str(error) or "allocation_within_net" in str(error)


def test_allocated_plus_refunded_cannot_exceed_net(db_session) -> None:
    error = assert_db_rejects(
        db_session,
        lambda: DonationFactory(
            net_usd_minor=1000, allocated_usd_minor=600, refunded_usd_minor=500
        ),
    )
    assert "ck_donations_allocation_within_net" in str(error)


def test_allocated_plus_refunded_up_to_net_is_accepted(db_session) -> None:
    donation = DonationFactory(net_usd_minor=1000, allocated_usd_minor=600, refunded_usd_minor=400)
    assert donation.allocated_usd_minor + donation.refunded_usd_minor == donation.net_usd_minor


def test_updating_allocation_beyond_net_is_refused_by_the_database(db_session) -> None:
    donation = DonationFactory(net_usd_minor=1000)
    db_session.flush()
    donation.allocated_usd_minor = 1001
    error = assert_db_rejects(db_session, lambda: None)
    assert "ck_donations_allocation_within_net" in str(error)


def test_donor_email_is_encrypted_at_rest(db_session) -> None:
    donation = DonationFactory(donor_email_enc=b"donor@example.test", donor_email_hmac="d" * 64)
    db_session.flush()
    stored = bytes(db_session.execute(text("SELECT donor_email_enc FROM donations")).scalar_one())
    assert b"donor@example.test" not in stored
    db_session.expire(donation)
    assert donation.donor_email_enc == b"donor@example.test"


def test_donation_repr_hides_donor_details(db_session) -> None:
    donation = DonationFactory(
        donor_display_name="Wanjiru K", donor_email_enc=b"donor@example.test"
    )
    assert "Wanjiru" not in repr(donation)
    assert "donor@" not in repr(donation)
    assert donation.receipt_token not in repr(donation)


def test_donation_links_case_and_rate_both_ways(db_session) -> None:
    rate = FxRateFactory()
    donation = DonationFactory(fx_rate=rate)
    db_session.flush()
    assert donation in donation.case.donations
    assert donation in rate.donations


def test_case_and_status_index_exists(db_session) -> None:
    definition = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_donations_case_status'")
    ).scalar_one()
    assert "(case_id, status)" in definition


def test_a_case_with_donations_cannot_be_deleted(db_session) -> None:
    donation = DonationFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(
            text("DELETE FROM cases WHERE id = :id"), {"id": donation.case_id}
        ),
    )
    assert "fk_donations_case_id_cases" in str(error)


# ---- Settlements ----


def test_settlement_defaults(db_session) -> None:
    settlement = SettlementFactory()
    db_session.refresh(settlement)
    assert settlement.status is SettlementStatus.PENDING
    assert settlement.adapter is SettlementMethod.PAYPAL_PAYOUTS
    assert settlement.external_batch_id is None
    assert settlement.completed_at is None
    assert settlement.requested_at is not None


def test_settlement_links_case_confirmation_and_rate(db_session) -> None:
    settlement = SettlementFactory()
    db_session.flush()
    assert settlement in settlement.case.settlements
    assert settlement in settlement.confirmation.settlements
    assert settlement in settlement.fx_rate.settlements


def test_tranche_numbers_are_unique_per_case_and_ordered(db_session) -> None:
    case = CaseFactory()
    second = SettlementFactory(case=case, tranche_no=2)
    first = SettlementFactory(case=case, tranche_no=1, confirmation=second.confirmation)
    db_session.flush()
    db_session.refresh(case)
    assert [s.tranche_no for s in case.settlements] == [1, 2]
    assert first.case is case
    error = assert_db_rejects(
        db_session,
        lambda: SettlementFactory(case=case, tranche_no=2, confirmation=second.confirmation),
    )
    assert "uq_settlements_case_id_tranche_no" in str(error)


def test_tranche_numbers_restart_for_another_case(db_session) -> None:
    SettlementFactory(tranche_no=1)
    assert SettlementFactory(tranche_no=1).tranche_no == 1


def test_idempotency_key_is_unique(db_session) -> None:
    SettlementFactory(idempotency_key="same-key")
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: SettlementFactory(idempotency_key="same-key"))
    assert "uq_settlements_idempotency_key" in str(error)


@pytest.mark.parametrize(
    ("column", "constraint"),
    [("amount_usd_minor", "amount_usd_positive"), ("amount_kes_minor", "amount_kes_positive")],
)
@pytest.mark.parametrize("value", [0, -1])
def test_settlement_amounts_must_be_positive(db_session, column, constraint, value) -> None:
    error = assert_db_rejects(db_session, lambda: SettlementFactory(**{column: value}))
    assert f"ck_settlements_{constraint}" in str(error)


def test_allocation_links_a_settlement_to_donations(db_session) -> None:
    settlement = SettlementFactory()
    first, second = DonationFactory(net_usd_minor=15_000), DonationFactory(net_usd_minor=15_000)
    db_session.flush()
    db_session.add_all(
        [
            SettlementAllocation(
                settlement_id=settlement.id, donation_id=first.id, amount_usd_minor=15_000
            ),
            SettlementAllocation(
                settlement_id=settlement.id, donation_id=second.id, amount_usd_minor=5_000
            ),
        ]
    )
    db_session.flush()
    db_session.refresh(settlement)
    assert sum(a.amount_usd_minor for a in settlement.allocations) == 20_000
    assert settlement.allocations[0].settlement is settlement
    db_session.refresh(first)
    assert [a.settlement_id for a in first.allocations] == [settlement.id]


def test_allocation_primary_key_is_the_pair(db_session) -> None:
    pk_columns = [c.name for c in SettlementAllocation.__table__.primary_key.columns]
    assert pk_columns == ["settlement_id", "donation_id"]


def test_a_donation_cannot_be_allocated_twice_to_the_same_settlement(db_session) -> None:
    settlement, donation = SettlementFactory(), DonationFactory(net_usd_minor=5_000)
    db_session.flush()
    db_session.add(
        SettlementAllocation(
            settlement_id=settlement.id, donation_id=donation.id, amount_usd_minor=1_000
        )
    )
    db_session.flush()
    duplicate = SettlementAllocation(
        settlement_id=settlement.id, donation_id=donation.id, amount_usd_minor=2_000
    )
    error = assert_db_rejects(db_session, lambda: db_session.add(duplicate))
    assert isinstance(error, IntegrityError)
    assert "pk_settlement_allocations" in str(error)


@pytest.mark.parametrize("amount", [0, -5])
def test_allocation_amount_must_be_positive(db_session, amount: int) -> None:
    settlement, donation = SettlementFactory(), DonationFactory()
    db_session.flush()
    allocation = SettlementAllocation(
        settlement_id=settlement.id, donation_id=donation.id, amount_usd_minor=amount
    )
    error = assert_db_rejects(db_session, lambda: db_session.add(allocation))
    assert "ck_settlement_allocations_amount_positive" in str(error)


def test_a_settlement_with_allocations_cannot_be_deleted(db_session) -> None:
    settlement, donation = SettlementFactory(), DonationFactory(net_usd_minor=5_000)
    db_session.flush()
    db_session.add(
        SettlementAllocation(
            settlement_id=settlement.id, donation_id=donation.id, amount_usd_minor=1_000
        )
    )
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(
            text("DELETE FROM settlements WHERE id = :id"), {"id": settlement.id}
        ),
    )
    assert "fk_settlement_allocations_settlement_id_settlements" in str(error)
    assert Settlement.__tablename__ == "settlements"


# ---- Refunds ----


def test_refund_defaults(db_session) -> None:
    refund = RefundFactory()
    db_session.refresh(refund)
    assert refund.status is RefundStatus.PENDING
    assert refund.paypal_refund_id is None
    assert refund.failure_reason is None
    assert refund.completed_at is None
    assert refund.reason is RefundReason.EXCESS_POLICY
    assert refund.requested_by is ActorType.SYSTEM


def test_refund_links_to_its_donation_both_ways(db_session) -> None:
    refund = RefundFactory()
    db_session.flush()
    assert refund in refund.donation.refunds


@pytest.mark.parametrize("amount", [0, -1])
def test_refund_amount_must_be_positive(db_session, amount: int) -> None:
    error = assert_db_rejects(db_session, lambda: RefundFactory(amount_usd_minor=amount))
    assert "ck_refunds_amount_positive" in str(error)


def test_refund_ids_are_unique_and_paypal_id_may_be_unset(db_session) -> None:
    RefundFactory(paypal_refund_id=None, idempotency_key="k1")
    RefundFactory(paypal_refund_id=None, idempotency_key="k2")
    RefundFactory(paypal_refund_id="REF-1", idempotency_key="k3")
    db_session.flush()
    dup_key = assert_db_rejects(db_session, lambda: RefundFactory(idempotency_key="k1"))
    assert "uq_refunds_idempotency_key" in str(dup_key)


def test_paypal_refund_id_is_unique(db_session) -> None:
    RefundFactory(paypal_refund_id="REF-1")
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: RefundFactory(paypal_refund_id="REF-1"))
    assert "uq_refunds_paypal_refund_id" in str(error)


def test_refund_enums_are_lowercase_native(db_session) -> None:
    RefundFactory(reason=RefundReason.FREEZE, requested_by=ActorType.AGENT)
    row = db_session.execute(
        text("SELECT reason::text, status::text, requested_by::text FROM refunds")
    )
    assert tuple(row.one()) == ("freeze", "pending", "agent")
