"""Applying a refund to the donation, ledger and case totals."""

import pytest
from sqlalchemy import select

from app.models.case import ReviewItem
from app.models.enums import (
    ActorType,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    RefundStatus,
)
from app.models.money import Refund
from app.services import ledger_service, refund_service
from tests.donation_helpers import donate, live_case
from tests.factories import RefundFactory


def entries(session, case):  # type: ignore[no-untyped-def]
    return list(session.execute(ledger_service.case_entries(case.id)).scalars())


def pending(donation, minor):  # type: ignore[no-untyped-def]
    return RefundFactory(
        donation=donation,
        amount_usd_minor=minor,
        reason=RefundReason.EXCESS_POLICY,
        status=RefundStatus.PENDING,
    )


def test_a_partial_refund_updates_the_donation_the_ledger_and_the_case(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case)
    refund = pending(donation, 1000)

    assert refund_service.complete_refund(
        db_session, refund, case, paypal_refund_id="REF-1", actor=ActorType.SYSTEM
    )

    assert (refund.status, refund.paypal_refund_id) == (RefundStatus.SUCCEEDED, "REF-1")
    assert refund.completed_at is not None
    assert (donation.refunded_usd_minor, donation.status) == (
        1000,
        DonationStatus.PARTIALLY_REFUNDED,
    )
    assert [(e.entry_type, e.account, e.amount_minor) for e in entries(db_session, case)[-2:]] == [
        (LedgerEntryType.REFUND_ISSUED, LedgerAccount.CASE_FUNDS, -1000),
        (LedgerEntryType.REFUND_ISSUED, LedgerAccount.DONOR_REFUNDS, 1000),
    ]
    assert {e.ref_id for e in entries(db_session, case)[-2:]} == {refund.id}
    # case_funds now holds what is left of the donation
    held = sum(
        e.amount_minor for e in entries(db_session, case) if e.account == LedgerAccount.CASE_FUNDS
    )
    assert held == donation.net_usd_minor - 1000
    assert case.refunded_usd_minor == 1000
    assert case.captured_net_usd_minor == 2397


def test_refunding_the_rest_makes_the_donation_refunded(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case)
    for minor, ref in ((1000, "R-1"), (1397, "R-2")):
        refund_service.complete_refund(
            db_session, pending(donation, minor), case, paypal_refund_id=ref, actor=ActorType.SYSTEM
        )

    assert donation.status == DonationStatus.REFUNDED
    assert case.pledged_kes_minor == 0
    assert ledger_service.verify_chain(db_session, case.id).valid


def test_completing_a_refund_twice_changes_nothing(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case)
    refund = pending(donation, 1000)
    refund_service.complete_refund(
        db_session, refund, case, paypal_refund_id="R-1", actor=ActorType.SYSTEM
    )

    again = refund_service.complete_refund(
        db_session, refund, case, paypal_refund_id="R-1", actor=ActorType.SYSTEM
    )

    assert again is False
    assert donation.refunded_usd_minor == 1000
    assert len(entries(db_session, case)) == 5


def test_a_refund_larger_than_what_the_donation_holds_is_refused(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case)
    donation.allocated_usd_minor = 2000  # most of it already went to the hospital
    refund = pending(donation, 500)

    with pytest.raises(ValueError, match="larger than what the donation still holds"):
        refund_service.complete_refund(
            db_session, refund, case, paypal_refund_id="R-1", actor=ActorType.SYSTEM
        )

    assert refund.status == RefundStatus.PENDING
    assert donation.refunded_usd_minor == 0


def test_the_refundable_amount_is_net_minus_paid_out_minus_refunded(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    donation = donate(db_session, live_case())
    donation.allocated_usd_minor = 1000
    donation.refunded_usd_minor = 300

    assert refund_service.refundable_usd_minor(donation) == 2397 - 1000 - 300


def test_pending_refunds_are_listed_oldest_first_and_only_pending(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case)
    first, second = pending(donation, 100), pending(donation, 200)
    done = pending(donation, 300)
    refund_service.complete_refund(
        db_session, done, case, paypal_refund_id="R-D", actor=ActorType.SYSTEM
    )

    assert refund_service.pending_for(db_session, donation) == [first, second]


def test_an_external_refund_of_a_known_id_is_not_recorded_twice(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    case = live_case()
    donation = donate(db_session, case)
    first = refund_service.record_external_refund(
        db_session, donation, case, paypal_refund_id="R-EXT", amount_usd_minor=500
    )

    second = refund_service.record_external_refund(
        db_session, donation, case, paypal_refund_id="R-EXT", amount_usd_minor=500
    )

    assert first is not None
    assert second is None
    assert db_session.execute(select(Refund)).scalars().all() == [first]
    assert donation.refunded_usd_minor == 500
    assert db_session.execute(select(ReviewItem)).first() is None
