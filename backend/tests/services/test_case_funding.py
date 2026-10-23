"""When a case counts as funded, reopens, and closes."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    ConfirmationStatus,
    DonationStatus,
    RefundReason,
    RefundStatus,
    SettlementStatus,
)
from app.models.platform import AuditLog
from app.services import case_service, mail_service, refund_service, settlement_service
from tests.donation_helpers import donate, settleable_case
from tests.factories import ConfirmationFactory, RefundFactory, SettlementFactory


@pytest.fixture(autouse=True)
def _manual(manual_settlement: list) -> None:
    """Each step is taken by hand."""


def transitions(session, case) -> list[tuple[str, str, str | None]]:  # type: ignore[no-untyped-def]
    rows = session.execute(
        select(AuditLog)
        .where(AuditLog.target_id == case.id, AuditLog.action == "case.transitioned")
        .order_by(AuditLog.created_at)
    ).scalars()
    return [(r.details["from"], r.details["to"], r.details.get("reason")) for r in rows]


# --------------------------------------------------------------------------- funded and back


def test_pledges_that_reach_the_goal_make_the_case_funded(db_session, paypal) -> None:
    case = settleable_case(target_kes_minor=100_000, buffer_bps=500)  # goal KES 1,050

    donate(db_session, case, "10.00")  # nets KES 1,214

    assert case.status == CaseStatus.FUNDED
    assert case.funded_at is not None
    assert transitions(db_session, case)[-1] == ("live", "funded", "goal_reached")


def test_pledges_exactly_at_the_goal_count_as_funded(db_session, paypal) -> None:
    case = settleable_case(target_kes_minor=100_000, buffer_bps=0)
    case.pledged_kes_minor = 100_000

    assert case_service.sync_funding(case) == CaseStatus.FUNDED


def test_one_cent_short_of_the_goal_is_still_live(db_session, paypal) -> None:
    case = settleable_case(target_kes_minor=100_000, buffer_bps=0)
    case.pledged_kes_minor = 99_999

    assert case_service.sync_funding(case) is None
    assert case.status == CaseStatus.LIVE


def test_a_funded_case_that_falls_below_its_goal_is_live_again(db_session, paypal) -> None:
    case = settleable_case(target_kes_minor=100_000, buffer_bps=500)
    donate(db_session, case, "10.00")
    assert case.status == CaseStatus.FUNDED
    case.pledged_kes_minor = 50_000

    assert case_service.sync_funding(case) == CaseStatus.LIVE

    assert case.status == CaseStatus.LIVE
    assert case.funded_at is None
    assert case_service.is_accepting_donations(case)
    assert transitions(db_session, case)[-1] == ("funded", "live", "below_goal")


@pytest.mark.parametrize(
    "status", [CaseStatus.FROZEN, CaseStatus.CANCELLED, CaseStatus.PENDING_CONFIRMATION]
)
def test_other_statuses_are_left_alone(db_session, paypal, status: CaseStatus) -> None:
    case = settleable_case(target_kes_minor=100_000, buffer_bps=0)
    case.status = status
    case.freeze_reason = "checking" if status == CaseStatus.FROZEN else None
    case.pledged_kes_minor = 500_000

    assert case_service.sync_funding(case) is None
    assert case.status == status


def test_a_refund_that_drops_the_pledges_below_the_goal_reopens_the_case(
    db_session, paypal
) -> None:
    case = settleable_case(target_kes_minor=100_000, buffer_bps=500)
    donation = donate(db_session, case, "10.00")
    assert case.status == CaseStatus.FUNDED
    refund = RefundFactory(
        donation=donation,
        amount_usd_minor=donation.net_usd_minor,
        reason=RefundReason.ADMIN,
        status=RefundStatus.PENDING,
    )

    refund_service.complete_refund(
        db_session, refund, case, paypal_refund_id="R-1", actor=ActorType.SYSTEM
    )

    assert case.status == CaseStatus.LIVE
    assert donation.status == DonationStatus.REFUNDED


def test_a_funded_case_takes_no_new_donations(db_session, paypal) -> None:
    case = settleable_case(target_kes_minor=100_000, buffer_bps=500)
    donate(db_session, case, "10.00")

    assert not case_service.is_accepting_donations(case)


# --------------------------------------------------------------------------- closing


def final_case(**kwargs):  # type: ignore[no-untyped-def]
    case = settleable_case(target_kes_minor=1_000_000, **kwargs)
    case.settled_kes_minor = 1_000_000
    return case


def test_a_final_bill_paid_in_full_closes_the_case(db_session, paypal) -> None:
    case = final_case()

    assert case_service.close_if_fully_settled(case) is True

    assert case.status == CaseStatus.CLOSED
    assert case.close_reason == CloseReason.FULLY_SETTLED
    assert case.closed_at is not None
    assert transitions(db_session, case)[-1] == ("live", "closed", "fully_settled")


def test_a_funded_case_closes_too(db_session, paypal) -> None:
    case = final_case()
    case.status = CaseStatus.FUNDED

    assert case_service.close_if_fully_settled(case) is True
    assert case.close_reason == CloseReason.FULLY_SETTLED


def test_an_estimate_never_closes_however_much_was_paid(db_session, paypal) -> None:
    case = final_case(is_estimate=True)
    case.settled_kes_minor = 1_000_000

    assert case_service.close_if_fully_settled(case) is False
    assert case.status == CaseStatus.LIVE


def test_a_bill_not_yet_paid_in_full_stays_open(db_session, paypal) -> None:
    case = final_case()
    case.settled_kes_minor = 999_999

    assert case_service.close_if_fully_settled(case) is False


@pytest.mark.parametrize("status", [SettlementStatus.PENDING, SettlementStatus.PROCESSING])
def test_a_payment_still_on_its_way_keeps_the_case_open(
    db_session, paypal, status: SettlementStatus
) -> None:
    case = final_case()
    SettlementFactory(case=case, status=status, confirmation=case.confirmations[0])
    db_session.expire(case, ["settlements"])

    assert case_service.close_if_fully_settled(case) is False


def test_a_failed_payment_does_not_keep_the_case_open(db_session, paypal) -> None:
    case = final_case()
    SettlementFactory(case=case, status=SettlementStatus.FAILED, confirmation=case.confirmations[0])
    db_session.expire(case, ["settlements"])

    assert case_service.close_if_fully_settled(case) is True


@pytest.mark.parametrize(
    "status", [CaseStatus.CLOSED, CaseStatus.FROZEN, CaseStatus.CANCELLED, CaseStatus.DRAFT]
)
def test_only_live_and_funded_cases_close(db_session, paypal, status: CaseStatus) -> None:
    case = final_case()
    case.status = status
    case.close_reason = "fully_settled" if status == CaseStatus.CLOSED else None
    case.freeze_reason = "checking" if status == CaseStatus.FROZEN else None

    assert case_service.close_if_fully_settled(case) is False


def test_a_case_without_a_target_does_not_close(db_session, paypal) -> None:
    case = settleable_case()
    case.target_kes_minor = None
    case.is_estimate = False

    assert case_service.close_if_fully_settled(case) is False


def test_a_final_bill_below_the_first_estimate_closes_as_final_bill_lower(
    db_session, paypal
) -> None:
    case = final_case()
    first = case.confirmations[0]
    first.amount_kes_minor = 1_500_000
    first.finalized_at = datetime.now(UTC) - timedelta(days=3)
    first.is_final_bill = False
    ConfirmationFactory(
        case=case, amount_kes_minor=1_000_000, is_final_bill=True, finalized_at=datetime.now(UTC)
    )
    db_session.expire(case, ["confirmations"])

    assert case_service.final_bill_was_lower(case)
    case_service.close_if_fully_settled(case)

    assert case.close_reason == CloseReason.FINAL_BILL_LOWER


def test_a_final_bill_equal_to_or_above_the_estimate_is_not_lower(db_session, paypal) -> None:
    case = final_case()
    case.confirmations[0].finalized_at = datetime.now(UTC) - timedelta(days=3)
    ConfirmationFactory(case=case, amount_kes_minor=1_200_000, finalized_at=datetime.now(UTC))
    db_session.expire(case, ["confirmations"])

    assert not case_service.final_bill_was_lower(case)


def test_queried_and_rejected_bills_are_ignored_when_judging_lower(db_session, paypal) -> None:
    case = final_case()
    ConfirmationFactory(case=case, status=ConfirmationStatus.QUERIED, amount_kes_minor=None)
    db_session.expire(case, ["confirmations"])

    assert not case_service.final_bill_was_lower(case)


def test_one_confirmation_alone_is_never_lower(db_session, paypal) -> None:
    assert not case_service.final_bill_was_lower(final_case())


# --------------------------------------------------------------------------- closing by payment


def test_the_last_payment_closes_the_case_and_tells_the_family_once(db_session, paypal) -> None:
    case = settleable_case(target_kes_minor=2_600_000)  # one $200 gift nets KES 25,013.10
    donate(db_session, case, "200.00")
    donate(db_session, case, "10.00")
    mail_service.outbox.clear()

    for _ in range(3):
        result = settlement_service.run_settlement_check(db_session, case.id)
        if result.settlement is None:
            break
        settlement_service.execute_settlement(db_session, result.settlement.id)
        settlement_service.poll_settlement(db_session, result.settlement.id)

    assert case.status == CaseStatus.CLOSED
    assert case.settled_kes_minor == 2_600_000
    closed = [m for m in mail_service.outbox if m.template == "case_closed"]
    assert [m.to for m in closed] == [case.created_by.email]
    assert "paid in full" in closed[0].text
    assert f"/c/{case.public_slug}" in closed[0].text
    assert "lower than the first estimate" not in closed[0].text


def test_a_payment_that_is_not_the_last_does_not_close_or_announce_it(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "200.00")
    mail_service.outbox.clear()

    result = settlement_service.run_settlement_check(db_session, case.id)
    settlement_service.execute_settlement(db_session, result.settlement.id)
    settlement_service.poll_settlement(db_session, result.settlement.id)

    assert case.status == CaseStatus.LIVE
    assert not [m for m in mail_service.outbox if m.template == "case_closed"]
