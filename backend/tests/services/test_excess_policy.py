"""Money a case did not need: each donor's choice, small amounts, holds, and the final books."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.case import ReviewItem
from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    RefundStatus,
    SettlementStatus,
)
from app.models.ledger import LedgerEntry
from app.models.money import Refund
from app.models.platform import AuditLog
from app.payments.errors import PayPalError
from app.services import (
    excess_policy_service,
    ledger_service,
    mail_service,
    refund_service,
    settlement_service,
)
from tests.donation_helpers import calls, donate, settleable_case


@pytest.fixture(autouse=True)
def _manual(manual_settlement: list, manual_refunds: list) -> None:
    """Settlement and refund jobs are driven by hand."""


def end(case, status: CaseStatus = CaseStatus.CLOSED) -> None:  # type: ignore[no-untyped-def]
    case.status = status
    case.close_reason = CloseReason.FULLY_SETTLED if status == CaseStatus.CLOSED else None
    if status == CaseStatus.CANCELLED:
        case.close_reason = CloseReason.TREATMENT_CANCELLED


def run(session, case, **kw):  # type: ignore[no-untyped-def]
    return excess_policy_service.run_excess_policy(session, case.id, **kw)


def entries(session, case) -> list[LedgerEntry]:  # type: ignore[no-untyped-def]
    return list(session.execute(ledger_service.case_entries(case.id)).scalars())


def case_funds(session, case) -> int:  # type: ignore[no-untyped-def]
    return sum(
        e.amount_minor for e in entries(session, case) if e.account == LedgerAccount.CASE_FUNDS
    )


def refunds_of(session, donation) -> list[Refund]:  # type: ignore[no-untyped-def]
    return list(session.execute(select(Refund).where(Refund.donation_id == donation.id)).scalars())


# --------------------------------------------------------------------------- the three choices


def test_a_donor_who_chose_a_refund_gets_the_leftover_back(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00", excess=ExcessChoice.REFUND)
    end(case)

    result = run(db_session, case)

    (refund,) = refunds_of(db_session, donation)
    assert result.refunds == [refund.id]
    assert manual_refunds == [refund.id]
    assert (refund.amount_usd_minor, refund.reason, refund.status) == (
        2397,
        RefundReason.EXCESS_POLICY,
        RefundStatus.PENDING,
    )
    assert refund.requested_by == ActorType.SYSTEM
    assert refund.idempotency_key == f"tiba-refund-{refund.id.hex}"
    assert donation.refunded_usd_minor == 0  # nothing has moved until PayPal says so


def test_the_refund_is_made_through_paypal_and_reaches_the_books(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    sent = calls(paypal, "refund_capture", monkeypatch)
    mail_service.outbox.clear()

    status = refund_service.execute_refund(db_session, refund.id)

    assert status == RefundStatus.SUCCEEDED
    ((args, kwargs),) = sent
    assert args == (donation.paypal_capture_id,)
    assert kwargs["amount_usd_minor"] == 2397
    assert kwargs["request_id"] == refund.idempotency_key
    assert "TibaDirect donation" in kwargs["note"]
    assert refund.paypal_refund_id.startswith("MOCKREF")
    assert refund.completed_at is not None
    assert (donation.refunded_usd_minor, donation.status) == (2397, DonationStatus.REFUNDED)
    refund_entries = [
        e for e in entries(db_session, case) if e.entry_type == LedgerEntryType.REFUND_ISSUED
    ]
    assert [(e.account, e.amount_minor) for e in refund_entries] == [
        (LedgerAccount.CASE_FUNDS, -2397),
        (LedgerAccount.DONOR_REFUNDS, 2397),
    ]
    assert case_funds(db_session, case) == 0
    (message,) = mail_service.outbox
    assert message.template == "refund_issued"
    assert "USD 23.97" in message.text
    assert case.alias in message.text


def test_a_refund_smaller_than_the_minimum_goes_to_the_pool(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "1.00")  # nets 67 cents: under the $1.00 minimum
    end(case)

    result = run(db_session, case)

    assert refunds_of(db_session, donation) == []
    assert manual_refunds == []
    assert result.pooled_usd_minor == 67
    assert donation.pooled_usd_minor == 67
    assert case_funds(db_session, case) == 0


def test_a_refund_of_exactly_the_minimum_is_still_a_refund(db_session, paypal, app) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "10.00")  # nets $9.41
    app.config["REFUND_MIN_USD_MINOR"] = donation.net_usd_minor
    try:
        end(case)
        run(db_session, case)
    finally:
        app.config["REFUND_MIN_USD_MINOR"] = 100

    assert len(refunds_of(db_session, donation)) == 1
    assert donation.pooled_usd_minor == 0


def test_a_donor_who_chose_the_pool_sees_it_move_there(db_session, paypal, manual_refunds) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00", excess=ExcessChoice.SOLIDARITY_POOL)
    end(case)

    result = run(db_session, case)

    assert refunds_of(db_session, donation) == []
    assert result.pooled_usd_minor == 2397
    assert (donation.pooled_usd_minor, donation.status) == (2397, DonationStatus.CAPTURED)
    moved = [e for e in entries(db_session, case) if e.entry_type == LedgerEntryType.POOL_TRANSFER]
    assert [(e.account, e.amount_minor, e.currency) for e in moved] == [
        (LedgerAccount.CASE_FUNDS, -2397, "USD"),
        (LedgerAccount.SOLIDARITY_POOL, 2397, "USD"),
    ]
    assert {e.ref_id for e in moved} == {donation.id}
    assert case_funds(db_session, case) == 0
    assert ledger_service.verify_chain(db_session, case.id).valid
    assert "donation.pooled" in [
        a
        for (a,) in db_session.execute(
            select(AuditLog.action).where(AuditLog.target_id == donation.id)
        )
    ]


def test_a_donor_who_chose_follow_up_care_has_the_money_held(
    db_session, paypal, manual_refunds, app
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(case)
    now = datetime.now(UTC)

    result = run(db_session, case, now=now)

    assert result.held_usd_minor == 2397
    assert refunds_of(db_session, donation) == []
    assert donation.pooled_usd_minor == 0
    assert case.follow_up_until == now + timedelta(days=14)
    assert case_funds(db_session, case) == 2397  # still with the case
    assert "case.follow_up_hold_started" in [
        a
        for (a,) in db_session.execute(select(AuditLog.action).where(AuditLog.target_id == case.id))
    ]


def test_inside_the_hold_nothing_changes_and_the_hold_is_not_restarted(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(case)
    start = datetime.now(UTC)
    run(db_session, case, now=start)

    again = run(db_session, case, now=start + timedelta(days=7))

    assert again.held_usd_minor == 2397
    assert case.follow_up_until == start + timedelta(days=14)
    assert manual_refunds == []


def test_when_the_hold_runs_out_the_money_is_refunded_like_any_refund(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(case)
    start = datetime.now(UTC)
    run(db_session, case, now=start)

    result = run(db_session, case, now=start + timedelta(days=15))

    (refund,) = refunds_of(db_session, donation)
    assert result.refunds == [refund.id]
    assert (refund.amount_usd_minor, refund.reason) == (2397, RefundReason.EXCESS_POLICY)


def test_a_follow_up_leftover_under_the_minimum_goes_to_the_pool_when_the_hold_ends(
    db_session, paypal
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "1.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(case)
    start = datetime.now(UTC)
    run(db_session, case, now=start)
    assert donation.pooled_usd_minor == 0

    run(db_session, case, now=start + timedelta(days=15))

    assert donation.pooled_usd_minor == 67


def test_each_donor_is_treated_as_they_chose_on_the_same_case(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    refund_me = donate(db_session, case, "25.00", excess=ExcessChoice.REFUND)
    pool_me = donate(db_session, case, "40.00", excess=ExcessChoice.SOLIDARITY_POOL)
    hold_me = donate(db_session, case, "30.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(case)

    result = run(db_session, case)

    assert [r.donation_id for r in db_session.execute(select(Refund)).scalars()] == [refund_me.id]
    assert pool_me.pooled_usd_minor == pool_me.net_usd_minor
    assert hold_me.pooled_usd_minor == 0
    assert (result.pooled_usd_minor, result.held_usd_minor) == (
        pool_me.net_usd_minor,
        hold_me.net_usd_minor,
    )


# --------------------------------------------------------------------------- only what is left


def test_money_already_paid_to_the_hospital_is_not_leftover(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case(target_kes_minor=2_500_000)
    donation = donate(db_session, case, "200.00")  # nets $193.90 = KES 25,013.10
    donate(db_session, case, "10.00")
    result = settlement_service.run_settlement_check(db_session, case.id)
    settlement_service.execute_settlement(db_session, result.settlement.id)
    settlement_service.poll_settlement(db_session, result.settlement.id)
    case.is_estimate = False
    end(case)

    run(db_session, case)

    paid = result.settlement.amount_usd_minor
    leftover = sum(d.net_usd_minor for d in case.donations) - paid
    refunded = sum(r.amount_usd_minor for r in db_session.execute(select(Refund)).scalars())
    pooled = sum(d.pooled_usd_minor for d in case.donations)
    assert refunded + pooled == leftover  # everything not paid out is placed, nothing more
    assert donation.allocated_usd_minor > 0
    assert pooled == 10  # the cents of the big gift the tranche did not need are too few to refund


def test_a_partly_refunded_donation_only_offers_what_is_left(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    donation.refunded_usd_minor = 1000
    end(case)

    run(db_session, case)

    (refund,) = refunds_of(db_session, donation)
    assert refund.amount_usd_minor == 1397


def test_a_donation_with_nothing_left_creates_nothing(db_session, paypal, manual_refunds) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    donation.allocated_usd_minor = donation.net_usd_minor
    end(case)

    result = run(db_session, case)

    assert (result.refunds, result.pooled_usd_minor, result.held_usd_minor) == ([], 0, 0)
    assert manual_refunds == []


def test_donations_that_were_never_taken_or_were_reversed_hold_nothing(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    reversed_ = donate(db_session, case, "25.00")
    reversed_.status = DonationStatus.REVERSED
    refunded = donate(db_session, case, "25.00")
    refunded.status = DonationStatus.REFUNDED
    refunded.refunded_usd_minor = refunded.net_usd_minor
    end(case)

    assert run(db_session, case).refunds == []


# --------------------------------------------------------------------------- cancellation and death


def test_a_cancelled_case_returns_leftovers_as_cancellation_refunds(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case, CaseStatus.CANCELLED)

    run(db_session, case)

    (refund,) = refunds_of(db_session, donation)
    assert refund.reason == RefundReason.CANCELLATION
    assert refund.amount_usd_minor == 2397


def test_a_cancelled_case_still_honours_a_pool_or_follow_up_choice(
    db_session, paypal, manual_refunds
) -> None:
    case = settleable_case()
    pooled = donate(db_session, case, "25.00", excess=ExcessChoice.SOLIDARITY_POOL)
    held = donate(db_session, case, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(case, CaseStatus.CANCELLED)
    case.close_reason = CloseReason.PATIENT_DECEASED

    run(db_session, case)

    assert pooled.pooled_usd_minor == pooled.net_usd_minor
    assert held.pooled_usd_minor == 0
    assert manual_refunds == []
    assert case.follow_up_until is not None


def test_money_is_never_sent_anywhere_the_donor_did_not_choose(db_session, paypal) -> None:
    case = settleable_case()
    donations = [
        donate(db_session, case, "25.00", excess=ExcessChoice.REFUND),
        donate(db_session, case, "25.00", excess=ExcessChoice.SOLIDARITY_POOL),
    ]
    end(case, CaseStatus.CANCELLED)
    run(db_session, case)

    assert donations[0].pooled_usd_minor == 0
    assert donations[1].refunded_usd_minor == 0
    assert refunds_of(db_session, donations[1]) == []


# --------------------------------------------------------------------------- safe to repeat


@pytest.mark.parametrize("status", [CaseStatus.LIVE, CaseStatus.FUNDED, CaseStatus.FROZEN])
def test_a_case_that_has_not_ended_is_left_alone(db_session, paypal, status: CaseStatus) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00")
    case.status = status
    case.freeze_reason = "checking" if status == CaseStatus.FROZEN else None
    db_session.commit()

    result = run(db_session, case)

    assert result.reason == "case_not_ended"
    assert db_session.execute(select(func.count()).select_from(Refund)).scalar_one() == 0


def test_running_twice_asks_for_one_refund_and_pools_once(db_session, paypal) -> None:
    case = settleable_case()
    refunded = donate(db_session, case, "25.00", excess=ExcessChoice.REFUND)
    pooled = donate(db_session, case, "25.00", excess=ExcessChoice.SOLIDARITY_POOL)
    end(case)

    run(db_session, case)
    again = run(db_session, case)

    assert (again.refunds, again.pooled_usd_minor) == ([], 0)
    assert len(refunds_of(db_session, refunded)) == 1
    assert pooled.pooled_usd_minor == pooled.net_usd_minor
    assert (
        len([e for e in entries(db_session, case) if e.entry_type == LedgerEntryType.POOL_TRANSFER])
        == 2
    )


def test_running_again_after_the_refund_was_made_does_nothing(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    refund_service.execute_refund(db_session, refund.id)

    assert run(db_session, case).refunds == []
    assert len(refunds_of(db_session, donation)) == 1


def test_a_failed_refund_frees_the_money_for_the_next_run(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    paypal.fail_next("refund_capture", http_status=422, name="UNPROCESSABLE_ENTITY", issue="OTHER")
    refund_service.execute_refund(db_session, refund.id)
    assert refund.status == RefundStatus.FAILED

    second = run(db_session, case)

    assert len(second.refunds) == 1  # a person was told; the system tries once more by itself


# --------------------------------------------------------------------------- making the refund


def test_executing_a_refund_twice_asks_paypal_once(db_session, paypal, monkeypatch) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    refund_service.execute_refund(db_session, refund.id)
    sent = calls(paypal, "refund_capture", monkeypatch)

    again = refund_service.execute_refund(db_session, refund.id)

    assert again == RefundStatus.SUCCEEDED
    assert sent == []
    assert donation.refunded_usd_minor == 2397


def test_a_paypal_outage_keeps_the_refund_pending_and_a_retry_uses_the_same_key(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    paypal.fail_next("refund_capture", http_status=503, name="SERVICE_UNAVAILABLE")

    with pytest.raises(PayPalError) as caught:
        refund_service.execute_refund(db_session, refund.id)
    assert caught.value.retryable
    db_session.refresh(refund)
    assert refund.status == RefundStatus.PENDING

    sent = calls(paypal, "refund_capture", monkeypatch)
    assert refund_service.execute_refund(db_session, refund.id) == RefundStatus.SUCCEEDED
    assert sent[0][1]["request_id"] == refund.idempotency_key


def test_a_misconfigured_account_leaves_the_refund_pending(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    paypal.fail_next("refund_capture", http_status=401, name="AUTHENTICATION_FAILURE")

    with pytest.raises(PayPalError):
        refund_service.execute_refund(db_session, refund.id)

    db_session.refresh(refund)
    assert refund.status == RefundStatus.PENDING


def test_a_refund_paypal_refuses_is_closed_as_failed_and_an_admin_is_asked(
    db_session, paypal
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    paypal.fail_next("refund_capture", issue="CAPTURE_FULLY_REFUNDED")

    status = refund_service.execute_refund(db_session, refund.id)

    assert status == RefundStatus.FAILED
    assert refund.failure_reason == "capture_fully_refunded"
    assert donation.refunded_usd_minor == 0
    item = db_session.execute(select(ReviewItem)).scalar_one()
    assert item.payload["refund_id"] == str(refund.id)
    assert mail_service.outbox == [] or all(
        m.template != "refund_issued" for m in mail_service.outbox
    )


def test_a_refund_paypal_calls_pending_waits_for_the_webhook(
    db_session, paypal, monkeypatch
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    monkeypatch.setattr(
        paypal, "refund_capture", lambda *a, **k: {"id": "REF-PENDING", "status": "PENDING"}
    )

    status = refund_service.execute_refund(db_session, refund.id)

    assert status == RefundStatus.PENDING
    assert refund.paypal_refund_id == "REF-PENDING"
    assert donation.refunded_usd_minor == 0
    # a second run of the policy sees the money as already promised
    assert run(db_session, case).refunds == []
    # the sweep leaves it for the webhook
    assert (
        refund_service.stale_pending_ids(db_session, datetime.now(UTC) + timedelta(hours=1)) == []
    )


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
def test_a_refund_paypal_fails_or_cancels_is_failed(db_session, paypal, monkeypatch, state) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    monkeypatch.setattr(paypal, "refund_capture", lambda *a, **k: {"id": "REF-X", "status": state})

    assert refund_service.execute_refund(db_session, refund.id) == RefundStatus.FAILED
    assert refund.failure_reason == state.lower()


def test_an_answer_without_an_id_is_a_bad_response(db_session, paypal, monkeypatch) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    monkeypatch.setattr(paypal, "refund_capture", lambda *a, **k: {"status": "COMPLETED"})

    with pytest.raises(PayPalError) as caught:
        refund_service.execute_refund(db_session, refund.id)

    assert caught.value.code == "paypal_bad_response"
    assert refund.status == RefundStatus.PENDING


def test_a_donation_without_a_paypal_payment_cannot_be_refunded(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    donation.paypal_capture_id = None

    assert refund_service.execute_refund(db_session, refund.id) == RefundStatus.FAILED


def test_an_unknown_refund_is_none(db_session) -> None:
    import uuid

    assert refund_service.execute_refund(db_session, uuid.uuid4()) is None


def test_a_donor_with_no_email_on_file_is_refunded_without_a_message(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    donation.donor_email_enc = None
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    mail_service.outbox.clear()

    refund_service.execute_refund(db_session, refund.id)

    assert mail_service.outbox == []
    assert refund.status == RefundStatus.SUCCEEDED


def test_a_failed_refund_email_never_undoes_the_refund(db_session, paypal, monkeypatch) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)

    def refuse(*_a, **_k):  # type: ignore[no-untyped-def]
        raise mail_service.MailError("down")

    monkeypatch.setattr(mail_service, "send", refuse)

    assert refund_service.execute_refund(db_session, refund.id) == RefundStatus.SUCCEEDED


# --------------------------------------------------------------------------- guards on creating


def test_a_refund_cannot_exceed_what_the_case_still_holds(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")

    with pytest.raises(refund_service.RefundTooLarge) as caught:
        refund_service.create_refund(db_session, donation, case, 2398, RefundReason.ADMIN)

    assert caught.value.code == "refund_exceeds_balance"
    assert caught.value.details["available_usd_minor"] == 2397


def test_a_refund_for_nothing_is_a_programming_error(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")

    with pytest.raises(ValueError, match="more than nothing"):
        refund_service.create_refund(db_session, donation, case, 0, RefundReason.ADMIN)


def test_money_promised_in_one_refund_cannot_be_promised_again(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    refund_service.create_refund(db_session, donation, case, 2000, RefundReason.ADMIN)

    with pytest.raises(refund_service.RefundTooLarge):
        refund_service.create_refund(db_session, donation, case, 500, RefundReason.ADMIN)
    refund_service.create_refund(db_session, donation, case, 397, RefundReason.ADMIN)

    assert refund_service.available_usd_minor(db_session, donation) == 0


def test_money_promised_to_a_refund_is_not_given_to_a_tranche(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "200.00")
    refund_service.create_refund(
        db_session, donation, case, donation.net_usd_minor, RefundReason.ADMIN
    )

    assert (
        settlement_service.run_settlement_check(db_session, case.id).reason == "nothing_available"
    )


def test_a_tranche_and_a_refund_together_never_exceed_the_donation(db_session, paypal) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "300.00")
    refund_service.create_refund(db_session, donation, case, 5000, RefundReason.ADMIN)

    result = settlement_service.run_settlement_check(db_session, case.id)

    assert result.settlement.amount_usd_minor == donation.net_usd_minor - 5000
    assert donation.allocated_usd_minor == donation.net_usd_minor - 5000


# --------------------------------------------------------------------------- the books at the end


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_when_every_leftover_is_placed_the_case_holds_no_money(
    db_session, paypal, seed: int
) -> None:
    """Whatever the mix of gifts, tranches and choices, the case account ends at exactly zero."""
    import random

    rng = random.Random(seed)
    case = settleable_case(is_estimate=False)
    choices = list(ExcessChoice)
    for _ in range(rng.randint(3, 6)):
        donate(
            db_session,
            case,
            rng.choice(["1.00", "20.00", "80.00", "150.00"]),
            excess=rng.choice(choices),
        )
        if case.status != CaseStatus.LIVE:
            break
    result = settlement_service.run_settlement_check(db_session, case.id)
    if result.settlement:
        settlement_service.execute_settlement(db_session, result.settlement.id)
        settlement_service.poll_settlement(db_session, result.settlement.id)
    case.status = CaseStatus.CANCELLED if seed % 2 else CaseStatus.CLOSED
    case.close_reason = CloseReason.ADMIN_CANCELLED if seed % 2 else CloseReason.FULLY_SETTLED
    db_session.commit()

    first = run(db_session, case)
    for refund_id in first.refunds:
        refund_service.execute_refund(db_session, refund_id)
    run(db_session, case, now=datetime.now(UTC) + timedelta(days=30))  # holds run out
    for refund in db_session.execute(
        select(Refund).where(Refund.status == RefundStatus.PENDING)
    ).scalars():
        refund_service.execute_refund(db_session, refund.id)

    assert case_funds(db_session, case) == 0
    assert ledger_service.verify_chain(db_session, case.id).valid
    for donation in case.donations:
        assert (
            donation.allocated_usd_minor + donation.refunded_usd_minor + donation.pooled_usd_minor
            <= donation.net_usd_minor
        )
        assert (
            donation.allocated_usd_minor + donation.refunded_usd_minor + donation.pooled_usd_minor
            == donation.net_usd_minor
        )


# --------------------------------------------------------------------------- queueing


def test_the_sweep_queues_ended_cases_with_money_left_and_leaves_the_rest(
    db_session, paypal, monkeypatch
) -> None:
    queued: list = []
    monkeypatch.setattr(excess_policy_service, "enqueue_run", queued.append)
    due = settleable_case()
    donate(db_session, due, "25.00")
    end(due)
    held = settleable_case()
    donate(db_session, held, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(held)
    held.follow_up_until = datetime.now(UTC) + timedelta(days=5)
    expired = settleable_case()
    donate(db_session, expired, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    end(expired)
    expired.follow_up_until = datetime.now(UTC) - timedelta(days=1)
    live = settleable_case()
    donate(db_session, live, "25.00")
    done = settleable_case()
    donor = donate(db_session, done, "25.00")
    donor.pooled_usd_minor = donor.net_usd_minor
    end(done)
    db_session.commit()

    count = excess_policy_service.sweep(db_session)

    assert sorted(queued) == sorted([due.id, expired.id])
    assert count == 2


def test_the_sweep_requeues_a_refund_nobody_has_made(
    db_session, paypal, monkeypatch, manual_refunds
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    end(case)
    run(db_session, case)
    (refund,) = refunds_of(db_session, donation)
    manual_refunds.clear()
    refund.created_at = datetime.now(UTC) - timedelta(minutes=10)
    db_session.commit()

    assert excess_policy_service.sweep(db_session) == 1
    assert manual_refunds == [refund.id]


def test_a_queue_outage_is_logged_not_raised(db_session, monkeypatch) -> None:
    from redis.exceptions import RedisError

    def down(*_a, **_k):  # type: ignore[no-untyped-def]
        raise RedisError("down")

    monkeypatch.setattr(excess_policy_service.jobs, "enqueue", down)
    monkeypatch.setattr(refund_service.jobs, "enqueue", down)
    import uuid

    excess_policy_service.enqueue_run(uuid.uuid4())  # does not raise
    refund_service.enqueue_execute(uuid.uuid4())  # does not raise


def test_a_settled_tranche_that_closes_the_case_starts_the_policy_by_itself(
    db_session, paypal, monkeypatch
) -> None:
    """The last payment closes the case; the leftovers are then placed with no manual step."""
    started: list = []
    monkeypatch.setattr(excess_policy_service, "enqueue_run", started.append)
    case = settleable_case(target_kes_minor=2_600_000)
    donate(db_session, case, "200.00")
    donate(db_session, case, "10.00")
    for _ in range(3):
        result = settlement_service.run_settlement_check(db_session, case.id)
        if result.settlement is None:
            break
        settlement_service.execute_settlement(db_session, result.settlement.id)
        settlement_service.poll_settlement(db_session, result.settlement.id)

    assert case.status == CaseStatus.CLOSED
    assert started == [case.id]
    assert case.settlements[-1].status == SettlementStatus.SUCCEEDED
