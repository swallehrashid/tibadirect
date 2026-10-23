"""The policy end to end through the (inline) queue: nobody presses a button."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.jobs import tasks
from app.models.enums import (
    CaseStatus,
    CloseReason,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    RefundStatus,
)
from app.models.money import Refund
from app.payments.paypal_mock import MockPayPal
from app.services import excess_policy_service, ledger_service, mail_service, settlement_service
from tests.donation_helpers import donate, settleable_case
from tests.webhook_helpers import deliver, make_event, refund_resource


def case_funds(session, case) -> int:  # type: ignore[no-untyped-def]
    return sum(
        e.amount_minor
        for e in session.execute(ledger_service.case_entries(case.id)).scalars()
        if e.account == LedgerAccount.CASE_FUNDS
    )


def test_a_case_that_closes_returns_what_it_did_not_need_without_anyone_asking(
    db_session, paypal: MockPayPal
) -> None:
    """Two gifts over the bill: the hospital is paid in full, the case closes, and the part of the
    second gift the bill did not need goes back to its donor, all by the queue alone."""
    case = settleable_case(target_kes_minor=2_600_000)  # KES 26,000, final
    donate(db_session, case, "200.00", excess=ExcessChoice.REFUND)  # nets $193.90, all used
    second = donate(db_session, case, "10.00", excess=ExcessChoice.REFUND)  # nets $9.41
    mail_service.outbox.clear()
    for _ in range(4):  # what the worker does after the gifts: poll payments that are due
        for s in case.settlements:
            s.updated_at = datetime.now(UTC) - timedelta(minutes=5)
        db_session.commit()
        settlement_service.poll_open_settlements(db_session)
        settlement_service.sweep(db_session)

    assert case.status == CaseStatus.CLOSED
    assert case.close_reason == CloseReason.FULLY_SETTLED
    assert case.settled_kes_minor == 2_600_000
    (refund,) = db_session.execute(select(Refund)).scalars().all()
    # the bill needed $7.66 of the second gift; the other $1.75 goes back
    assert (refund.donation_id, refund.amount_usd_minor) == (second.id, 175)
    assert refund.status == RefundStatus.SUCCEEDED
    assert second.status == DonationStatus.PARTIALLY_REFUNDED
    assert case_funds(db_session, case) == 0
    assert ledger_service.verify_chain(db_session, case.id).valid
    sent = {m.template: m.to for m in mail_service.outbox}
    assert sent["refund_issued"] == "mock.donor@example.test"
    assert sent["case_closed"] == case.created_by.email


def test_the_hourly_job_refunds_a_follow_up_hold_that_has_run_out(
    db_session, paypal: MockPayPal, manual_settlement
) -> None:
    case = settleable_case()
    donation = donate(db_session, case, "25.00", excess=ExcessChoice.FOLLOW_UP_CARE)
    case.status = CaseStatus.CLOSED
    case.close_reason = CloseReason.FULLY_SETTLED
    db_session.commit()
    excess_policy_service.run_excess_policy(db_session, case.id)
    assert donation.pooled_usd_minor == 0
    assert donation.refunded_usd_minor == 0
    case.follow_up_until = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    queued = tasks.expire_follow_up_holds()

    assert queued == 1
    db_session.refresh(donation)
    assert (donation.refunded_usd_minor, donation.status) == (2397, DonationStatus.REFUNDED)
    assert case_funds(db_session, case) == 0


def test_the_jobs_take_ids_and_report_what_they_did(
    db_session, paypal: MockPayPal, manual_refunds, manual_settlement
) -> None:
    case = settleable_case()
    donate(db_session, case, "25.00")
    case.status = CaseStatus.CLOSED
    case.close_reason = CloseReason.FULLY_SETTLED
    db_session.commit()

    summary = tasks.run_excess_policy(str(case.id))

    assert summary == {
        "reason": "ran",
        "refunds": 1,
        "pooled_usd_minor": 0,
        "held_usd_minor": 0,
    }
    assert tasks.execute_refund(manual_refunds[0]) == "succeeded"
    assert tasks.execute_refund(manual_refunds[0]) == "succeeded"  # a repeat asks PayPal nothing
    import uuid

    assert tasks.execute_refund(uuid.uuid4()) is None


def test_a_refund_paypal_called_pending_is_finished_by_its_webhook(
    client, db_session, paypal: MockPayPal, manual_refunds, manual_settlement, monkeypatch
) -> None:
    from app.services import refund_service

    case = settleable_case()
    donation = donate(db_session, case, "25.00")
    case.status = CaseStatus.CLOSED
    case.close_reason = CloseReason.FULLY_SETTLED
    db_session.commit()
    excess_policy_service.run_excess_policy(db_session, case.id)
    (refund,) = db_session.execute(select(Refund)).scalars().all()
    monkeypatch.setattr(
        paypal, "refund_capture", lambda *a, **k: {"id": "REF-LATER", "status": "PENDING"}
    )
    refund_service.execute_refund(db_session, refund.id)
    assert refund.status == RefundStatus.PENDING
    mail_service.outbox.clear()

    response = deliver(
        client, make_event("PAYMENT.CAPTURE.REFUNDED", refund_resource(donation, "REF-LATER", 2397))
    )

    assert response.status_code == 200
    db_session.refresh(refund)
    db_session.refresh(donation)
    assert refund.status == RefundStatus.SUCCEEDED
    assert donation.status == DonationStatus.REFUNDED
    assert case_funds(db_session, case) == 0
