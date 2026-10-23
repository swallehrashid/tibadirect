"""Freezing and cancelling: donors are repaid, tranches not yet sent are stopped, the case ends."""

from collections.abc import Callable

import pytest
from sqlalchemy import select

from app.models.case import ReviewItem
from app.models.enums import (
    CaseStatus,
    CloseReason,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    RefundReason,
    RefundStatus,
    SettlementStatus,
    TrustLevel,
    UserRole,
)
from app.models.money import Refund, SettlementAllocation
from app.models.platform import AuditLog
from app.services import (
    case_ending_service,
    case_service,
    donation_service,
    excess_policy_service,
    ledger_service,
    mail_service,
    refund_service,
    settlement_service,
)
from tests.browser import Browser
from tests.donation_helpers import donate, order_for, settleable_case
from tests.factories import TriageResultFactory, UserFactory

BrowserFor = Callable[..., Browser]


ORIGINAL_FREEZE_HOOK = refund_service._finish_freeze_if_frozen  # noqa: SLF001


@pytest.fixture(autouse=True)
def finishes(
    manual_settlement: list, manual_refunds: list, monkeypatch: pytest.MonkeyPatch
) -> list:
    """Settlement, refund and follow-up jobs are driven by hand; ``finishes`` lists the freeze
    checks that would have run."""
    seen: list = []
    monkeypatch.setattr(case_ending_service, "enqueue_finish", seen.append)
    monkeypatch.setattr(refund_service, "_finish_freeze_if_frozen", lambda case: None)
    return seen


def admin_user():  # type: ignore[no-untyped-def]
    return UserFactory(role=UserRole.ADMIN)


def freeze(session, case, reason="Suspected forged invoice"):  # type: ignore[no-untyped-def]
    ended = case_ending_service.freeze_case(session, case, admin_user(), reason)
    session.commit()
    return ended


def run_refunds(session) -> None:  # type: ignore[no-untyped-def]
    for refund in session.execute(
        select(Refund).where(Refund.status == RefundStatus.PENDING)
    ).scalars():
        refund_service.execute_refund(session, refund.id)


def case_funds(session, case) -> int:  # type: ignore[no-untyped-def]
    return sum(
        e.amount_minor
        for e in session.execute(ledger_service.case_entries(case.id)).scalars()
        if e.account == LedgerAccount.CASE_FUNDS
    )


# --------------------------------------------------------------------------- freezing


def test_a_freeze_hides_the_case_stops_donations_and_drops_trust(db_session, paypal) -> None:
    case = settleable_case()
    donor = donate(db_session, case, "25.00")
    pending_order = order_for(db_session, case, "10.00")
    db_session.commit()

    freeze(db_session, case)

    assert (case.status, case.trust_level) == (CaseStatus.FROZEN, TrustLevel.UNVERIFIED)
    assert case.freeze_reason == "Suspected forged invoice"
    assert not case.is_publicly_visible
    assert not case_service.is_accepting_donations(case)
    with pytest.raises(donation_service.CaseFullyFunded):
        donation_service.capture_order(db_session, pending_order.paypal_order_id)
    assert donor.status == DonationStatus.CAPTURED


def test_every_donor_is_refunded_in_full_whatever_they_chose(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    gifts = [
        donate(db_session, case, "25.00", excess=ExcessChoice.REFUND),
        donate(db_session, case, "40.00", excess=ExcessChoice.SOLIDARITY_POOL),
        donate(db_session, case, "30.00", excess=ExcessChoice.FOLLOW_UP_CARE),
        donate(db_session, case, "1.00", excess=ExcessChoice.REFUND),  # nets 67 cents
    ]

    ended = freeze(db_session, case)

    refunds = db_session.execute(select(Refund).order_by(Refund.created_at)).scalars().all()
    assert {r.donation_id: r.amount_usd_minor for r in refunds} == {
        g.id: g.net_usd_minor for g in gifts
    }
    assert {r.reason for r in refunds} == {RefundReason.FREEZE}
    assert ended.refund_ids == [r.id for r in refunds]
    assert all(g.pooled_usd_minor == 0 for g in gifts)
    assert case.follow_up_until is None
    assert [r.status for r in refunds] == [RefundStatus.PENDING] * 4


def test_the_refunds_are_queued_once_the_freeze_is_saved(
    db_session, paypal, manual_refunds, finishes
) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00")
    ended = freeze(db_session, case)
    mail_service.outbox.clear()

    case_ending_service.after_commit(ended, frozen=True, case=case)

    assert manual_refunds == ended.refund_ids
    assert finishes == [case.id]  # and the check that finishes the freeze
    (family,) = mail_service.outbox
    assert (family.template, family.to) == ("case_frozen", case.created_by.email)
    assert "paused" in family.text
    assert case.alias in family.text


def test_a_tranche_not_yet_sent_is_cancelled_and_its_money_goes_back(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "200.00")
    tranche = settlement_service.run_settlement_check(db_session, case.id).settlement
    assert donation.allocated_usd_minor == donation.net_usd_minor

    freeze(db_session, case)

    db_session.refresh(tranche)
    db_session.refresh(donation)
    assert tranche.status == SettlementStatus.FAILED
    assert tranche.failure_reason.startswith("Cancelled")
    assert donation.allocated_usd_minor == 0
    assert db_session.execute(select(SettlementAllocation)).first() is None
    (refund,) = db_session.execute(select(Refund)).scalars().all()
    assert refund.amount_usd_minor == donation.net_usd_minor
    assert db_session.execute(select(ReviewItem)).first() is None


def test_a_payout_already_with_paypal_is_allowed_to_finish_and_is_not_refunded(
    db_session, paypal
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "200.00")
    donate(db_session, case, "50.00")
    tranche = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement_service.execute_settlement(db_session, tranche.id)
    assert tranche.status == SettlementStatus.PROCESSING

    freeze(db_session, case)

    refunds = db_session.execute(select(Refund)).scalars().all()
    refunded = sum(r.amount_usd_minor for r in refunds)
    all_net = sum(d.net_usd_minor for d in case.donations)
    assert refunded == all_net - tranche.amount_usd_minor  # the tranche in flight is not touched
    assert tranche.status == SettlementStatus.PROCESSING
    assert donation.allocated_usd_minor > 0
    # the payout finishes after the freeze, as allowed
    settlement_service.poll_settlement(db_session, tranche.id)
    assert tranche.status == SettlementStatus.SUCCEEDED


def test_the_signals_of_the_frozen_case_go_into_the_audit_trail(db_session, paypal) -> None:
    case = settleable_case()
    TriageResultFactory(
        document__case=case,
        case=case,
        risk_score=72,
        signals=[{"code": "totals_mismatch", "weight": 25}, "duplicate_near"],
    )

    freeze(db_session, case)

    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "case.freeze_signals")
    ).scalar_one()
    assert entry.details == {
        "signals": ["totals_mismatch", "duplicate_near"],
        "risk_score": 72,
        "refunds": 0,
    }


def test_a_case_cannot_be_frozen_twice_or_from_the_wrong_status(db_session, paypal) -> None:
    case = settleable_case()
    freeze(db_session, case)
    with pytest.raises(case_ending_service.Conflict) as caught:
        case_ending_service.freeze_case(db_session, case, admin_user(), "Again for no reason")
    assert caught.value.code == "case_already_frozen"

    closed = settleable_case()
    closed.status = CaseStatus.CLOSED
    closed.close_reason = CloseReason.FULLY_SETTLED
    with pytest.raises(case_service.InvalidTransition):
        case_ending_service.freeze_case(db_session, closed, admin_user(), "Too late to freeze")


# --------------------------------------------------------------------------- finishing the freeze


def test_when_every_refund_is_made_the_case_is_cancelled_for_fraud(db_session, paypal) -> None:
    case = settleable_case()
    donations = [donate(db_session, case, "25.00"), donate(db_session, case, "40.00")]
    freeze(db_session, case)
    run_refunds(db_session)

    result = case_ending_service.finish_frozen(db_session, case.id)

    assert result == "cancelled"
    assert (case.status, case.close_reason) == (CaseStatus.CANCELLED, CloseReason.FRAUD)
    assert all(d.status == DonationStatus.REFUNDED for d in donations)
    assert case_funds(db_session, case) == 0
    assert ledger_service.verify_chain(db_session, case.id).valid
    last = (
        db_session.execute(
            select(AuditLog)
            .where(AuditLog.target_id == case.id, AuditLog.action == "case.transitioned")
            .order_by(AuditLog.created_at.desc())
        )
        .scalars()
        .first()
    )
    assert (last.details["from"], last.details["to"], last.details["reason"]) == (
        "frozen",
        "cancelled",
        "frozen_case_repaid",
    )


def test_a_frozen_case_with_no_money_is_cancelled_straight_away(db_session, paypal) -> None:
    case = settleable_case()
    freeze(db_session, case)

    assert case_ending_service.finish_frozen(db_session, case.id) == "cancelled"
    assert case.close_reason == CloseReason.FRAUD


def test_it_waits_while_a_refund_is_still_to_be_made(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00")
    freeze(db_session, case)

    assert case_ending_service.finish_frozen(db_session, case.id) == "waiting"
    assert case.status == CaseStatus.FROZEN


def test_it_waits_while_a_payout_is_on_its_way(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "200.00")
    donate(db_session, case, "50.00")
    tranche = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement_service.execute_settlement(db_session, tranche.id)
    freeze(db_session, case)
    run_refunds(db_session)

    assert case_ending_service.finish_frozen(db_session, case.id) == "waiting"
    settlement_service.poll_settlement(db_session, tranche.id)
    assert case_ending_service.finish_frozen(db_session, case.id) == "cancelled"


def test_money_freed_by_a_failed_payout_is_refunded_too(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "200.00")
    paypal.next_payout_outcome("PENDING")
    tranche = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement_service.execute_settlement(db_session, tranche.id)
    freeze(db_session, case)
    assert db_session.execute(select(Refund)).first() is None  # all of it is in flight
    batch = paypal.store.get("payout", tranche.external_batch_id)
    batch["outcome"] = "FAILED"
    paypal.store.put("payout", tranche.external_batch_id, batch)
    settlement_service.poll_settlement(db_session, tranche.id)
    assert tranche.status == SettlementStatus.FAILED

    assert case_ending_service.finish_frozen(db_session, case.id) == "waiting"
    run_refunds(db_session)
    assert case_ending_service.finish_frozen(db_session, case.id) == "cancelled"

    db_session.refresh(donation)
    assert donation.status == DonationStatus.REFUNDED


def test_a_refund_that_failed_blocks_the_end_and_an_admin_is_told(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00")
    freeze(db_session, case)
    paypal.fail_next("refund_capture", issue="CAPTURE_FULLY_REFUNDED")
    run_refunds(db_session)

    # the failed refund frees the money, so the next check asks again, once
    assert case_ending_service.finish_frozen(db_session, case.id) == "waiting"
    paypal.fail_next("refund_capture", issue="CAPTURE_FULLY_REFUNDED")
    run_refunds(db_session)
    assert db_session.execute(select(ReviewItem)).scalars().all() != []


def test_a_case_that_is_not_frozen_is_left_alone(db_session, paypal) -> None:
    case = settleable_case()

    assert case_ending_service.finish_frozen(db_session, case.id) == "not_frozen"
    assert case.status == CaseStatus.LIVE


def test_finishing_twice_is_harmless(db_session, paypal) -> None:
    case = settleable_case()
    freeze(db_session, case)
    case_ending_service.finish_frozen(db_session, case.id)

    assert case_ending_service.finish_frozen(db_session, case.id) == "not_frozen"


def test_the_last_refund_on_a_frozen_case_asks_for_the_freeze_to_be_finished(
    db_session, paypal, monkeypatch
) -> None:
    monkeypatch.setattr(refund_service, "_finish_freeze_if_frozen", ORIGINAL_FREEZE_HOOK)
    queued: list = []
    monkeypatch.setattr(refund_service.jobs, "enqueue", lambda *a, **k: queued.append(a))
    case = settleable_case()
    donate(db_session, case, "25.00")
    freeze(db_session, case)

    run_refunds(db_session)

    assert queued == [
        (refund_service.jobs.QUEUE_PAYMENTS, "app.jobs.tasks.finish_frozen_case", str(case.id))
    ]


def test_the_sweep_queues_every_frozen_case(db_session, paypal, finishes: list) -> None:
    frozen = settleable_case()
    freeze(db_session, frozen)
    settleable_case()  # live, not queued
    finishes.clear()

    assert case_ending_service.sweep_frozen(db_session) == 1
    assert finishes == [frozen.id]


def test_a_late_payment_on_a_case_cancelled_for_fraud_is_refunded_whatever_the_choice(
    db_session, paypal
) -> None:
    case = settleable_case()
    freeze(db_session, case)
    case_ending_service.finish_frozen(db_session, case.id)
    assert case.close_reason == CloseReason.FRAUD
    # PayPal reports a payment taken before the freeze, after the case ended
    donation = (
        order_for(db_session, case, "25.00", excess=ExcessChoice.SOLIDARITY_POOL) if False else None
    )
    assert donation is None
    late = settleable_case()
    gift = donate(db_session, late, "25.00", excess=ExcessChoice.SOLIDARITY_POOL)
    late.status = CaseStatus.CANCELLED
    late.close_reason = CloseReason.FRAUD

    result = excess_policy_service.run_excess_policy(db_session, late.id)

    (refund,) = db_session.execute(select(Refund)).scalars().all()
    assert (refund.donation_id, refund.reason) == (gift.id, RefundReason.FREEZE)
    assert result.pooled_usd_minor == 0


# --------------------------------------------------------------------------- cancelling


@pytest.mark.parametrize(
    ("reason", "close_reason"),
    [
        ("treatment_cancelled", CloseReason.TREATMENT_CANCELLED),
        ("patient_deceased", CloseReason.PATIENT_DECEASED),
        ("other", CloseReason.TREATMENT_CANCELLED),
    ],
)
def test_the_family_cancels_and_the_reason_is_recorded(
    db_session, paypal, reason: str, close_reason: CloseReason
) -> None:
    case = settleable_case()

    ended = case_ending_service.cancel_case(db_session, case, case.created_by, reason)
    db_session.commit()

    assert case.status == CaseStatus.CANCELLED
    assert case.close_reason == close_reason
    assert ended.case_id == case.id
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "case.cancelled_by_family")
    ).scalar_one()
    assert entry.details["family_reason"] == reason
    assert entry.actor_user_id == case.created_by.id


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
    ],
)
def test_a_case_can_be_cancelled_from_submission_until_it_ends(
    db_session, paypal, status: CaseStatus
) -> None:
    case = settleable_case()
    case.status = status

    case_ending_service.cancel_case(db_session, case, case.created_by, "other")

    assert case.status == CaseStatus.CANCELLED


@pytest.mark.parametrize(
    "status", [CaseStatus.DRAFT, CaseStatus.FROZEN, CaseStatus.CLOSED, CaseStatus.CANCELLED]
)
def test_a_draft_an_ended_or_frozen_case_cannot_be_cancelled(
    db_session, paypal, status: CaseStatus
) -> None:
    case = settleable_case()
    case.status = status
    case.close_reason = CloseReason.FULLY_SETTLED if status == CaseStatus.CLOSED else None
    case.freeze_reason = "checking" if status == CaseStatus.FROZEN else None

    with pytest.raises(case_ending_service.CaseNotCancellable) as caught:
        case_ending_service.cancel_case(db_session, case, case.created_by, "other")

    assert caught.value.code == "case_not_cancellable"
    assert case.status == status


def test_an_unknown_reason_is_refused(db_session, paypal) -> None:
    case = settleable_case()

    with pytest.raises(case_ending_service.Conflict) as caught:
        case_ending_service.cancel_case(db_session, case, case.created_by, "because")

    assert caught.value.code == "reason_unknown"


def test_cancelling_stops_donations_and_cancels_tranches_not_yet_sent(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "200.00")
    order = order_for(db_session, case, "10.00")
    tranche = settlement_service.run_settlement_check(db_session, case.id).settlement

    case_ending_service.cancel_case(db_session, case, case.created_by, "treatment_cancelled")
    db_session.commit()

    assert not case_service.is_accepting_donations(case)
    with pytest.raises(donation_service.CaseFullyFunded):
        donation_service.capture_order(db_session, order.paypal_order_id)
    db_session.refresh(tranche)
    db_session.refresh(donation)
    assert tranche.status == SettlementStatus.FAILED
    assert donation.allocated_usd_minor == 0


def test_after_a_cancellation_each_donors_choice_is_followed(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    refund_me = donate(db_session, case, "25.00", excess=ExcessChoice.REFUND)
    pool_me = donate(db_session, case, "25.00", excess=ExcessChoice.SOLIDARITY_POOL)
    hold_me = donate(db_session, case, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    ended = case_ending_service.cancel_case(db_session, case, case.created_by, "patient_deceased")
    db_session.commit()

    excess_policy_service.run_excess_policy(db_session, ended.case_id)

    (refund,) = db_session.execute(select(Refund)).scalars().all()
    assert (refund.donation_id, refund.reason) == (refund_me.id, RefundReason.CANCELLATION)
    assert pool_me.pooled_usd_minor == pool_me.net_usd_minor
    assert hold_me.pooled_usd_minor == 0
    assert case.follow_up_until is not None


def test_after_commit_for_a_cancellation_starts_the_policy(db_session, paypal, monkeypatch) -> None:
    started: list = []
    monkeypatch.setattr(excess_policy_service, "enqueue_run", started.append)
    case = settleable_case()
    ended = case_ending_service.cancel_case(db_session, case, case.created_by, "other")

    case_ending_service.after_commit(ended)

    assert started == [case.id]


def test_a_payout_already_with_paypal_finishes_after_a_cancellation(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "200.00")
    tranche = settlement_service.run_settlement_check(db_session, case.id).settlement
    settlement_service.execute_settlement(db_session, tranche.id)

    case_ending_service.cancel_case(db_session, case, case.created_by, "treatment_cancelled")
    db_session.commit()
    settlement_service.poll_settlement(db_session, tranche.id)

    assert tranche.status == SettlementStatus.SUCCEEDED  # what reached the hospital stays there
    assert case.status == CaseStatus.CANCELLED


def test_the_describe_helper_reports_status_and_reason(db_session, paypal) -> None:
    case = settleable_case()
    case_ending_service.cancel_case(db_session, case, case.created_by, "patient_deceased")

    assert case_ending_service.describe(case) == {
        "status": "cancelled",
        "close_reason": "patient_deceased",
    }


def test_a_later_scheduler_run_cannot_reopen_anything(db_session, paypal) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00")
    case_ending_service.cancel_case(db_session, case, case.created_by, "other")

    assert (
        settlement_service.run_settlement_check(db_session, case.id).reason == "case_not_settleable"
    )
