"""When PayPal is down, money jobs fail, wait and try again, and each payment happens once.

These run the real queue and worker: the job fails, the retry policy schedules it, the provider
comes back, the job runs again. The ten-second wait is stepped over by hand. What is checked is
the money, not the job: one payout batch at the provider, one refund, one ledger entry each.
"""

from flask import Flask
from rq.job import JobStatus
from sqlalchemy import func, select

from app.jobs import queue as jobs
from app.jobs.worker import make_worker
from app.models.enums import LedgerEntryType, RefundStatus, SettlementStatus
from app.models.ledger import LedgerEntry
from app.models.money import Donation, Refund, Settlement
from app.payments.paypal_mock import MockPayPal
from app.services import case_ending_service, settlement_service
from tests.donation_helpers import donate, settleable_case


def _after_the_wait(job) -> None:  # type: ignore[no-untyped-def]
    """What RQ's scheduler does ten seconds after a failure: put the job back on its queue."""
    queue = jobs.get_queue(job.origin)
    assert job.get_status() == JobStatus.SCHEDULED
    queue.scheduled_job_registry.remove(job)
    queue.enqueue_job(job)


def test_a_payout_sent_while_paypal_is_down_goes_out_once_when_it_is_back(
    app: Flask, async_jobs: None, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    sent: list = []
    original = paypal.create_payout

    def spy(**kwargs):  # type: ignore[no-untyped-def]
        sent.append(kwargs["sender_batch_id"])
        return original(**kwargs)

    monkeypatch.setattr(paypal, "create_payout", spy)
    case = settleable_case()
    donate(db_session, case, "200.00")
    settlement = settlement_service.run_settlement_check(db_session, case.id).settlement
    db_session.commit()
    jobs.get_queue(jobs.QUEUE_PAYMENTS).empty()  # forget the job the check queued; run our own

    paypal.fail_next("create_payout", http_status=503, name="SERVICE_UNAVAILABLE")
    job = jobs.enqueue(jobs.QUEUE_PAYMENTS, "app.jobs.tasks.execute_settlement", str(settlement.id))
    make_worker(app).work(burst=True)  # PayPal answers 503: the job fails and is scheduled again

    job.refresh()
    assert job.retries_left == jobs.RETRY_MAX - 1
    db_session.expire_all()
    assert db_session.get(Settlement, settlement.id).status == SettlementStatus.PENDING

    _after_the_wait(job)
    make_worker(app).work(burst=True)  # PayPal is back

    db_session.expire_all()
    assert db_session.get(Settlement, settlement.id).status == SettlementStatus.PROCESSING
    assert len(sent) == 2  # one attempt that failed, one that went through ...
    assert len(set(sent)) == 1  # ... under the same key, so the provider made one batch

    # Running it yet again (a duplicate message, an operator's retry) sends nothing more.
    again = jobs.enqueue(
        jobs.QUEUE_PAYMENTS, "app.jobs.tasks.execute_settlement", str(settlement.id)
    )
    make_worker(app).work(burst=True)
    again.refresh()
    assert again.get_status() == JobStatus.FINISHED
    assert len(sent) == 2
    ledger = db_session.execute(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.entry_type == LedgerEntryType.SETTLEMENT_SENT)
    ).scalar_one()
    assert ledger == 0  # nothing is booked as paid until the provider says it is paid


def test_a_refund_made_while_paypal_is_down_is_made_once_when_it_is_back(
    app: Flask, async_jobs: None, db_session, paypal: MockPayPal, monkeypatch
) -> None:
    made: list = []
    original = paypal.refund_capture

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        made.append(kwargs.get("request_id") or args)
        return original(*args, **kwargs)

    monkeypatch.setattr(paypal, "refund_capture", spy)
    case = settleable_case()
    donate(db_session, case, "50.00")
    db_session.commit()
    jobs.get_queue(jobs.QUEUE_PAYMENTS).empty()
    jobs.get_queue(jobs.QUEUE_DEFAULT).empty()

    paypal.fail_next("refund_capture", http_status=503, name="SERVICE_UNAVAILABLE")
    admin = case.created_by  # any actor: the case is ended by its owner here
    ended = case_ending_service.cancel_case(db_session, case, admin, "treatment_cancelled")
    db_session.commit()
    case_ending_service.after_commit(ended)  # queues the excess policy, as the route does
    queue = jobs.get_queue(jobs.QUEUE_PAYMENTS)
    make_worker(app).work(
        burst=True
    )  # the policy makes the refund; its first attempt meets the outage
    refunds = list(
        db_session.execute(
            select(Refund).join(Donation).where(Donation.case_id == case.id)
        ).scalars()
    )
    assert len(refunds) == 1
    scheduled = [queue.fetch_job(job_id) for job_id in queue.scheduled_job_registry.get_job_ids()]
    assert scheduled, "the failed refund is waiting for its retry"
    for job in scheduled:
        _after_the_wait(job)
    make_worker(app).work(burst=True)

    db_session.expire_all()
    refund = db_session.get(Refund, refunds[0].id)
    assert refund.status == RefundStatus.SUCCEEDED
    assert len(made) == 2  # one attempt that met the outage, one that went through
    paid = _refund_lines(db_session)
    assert paid > 0

    # A duplicate message for the same refund changes nothing.
    again = jobs.enqueue(jobs.QUEUE_PAYMENTS, "app.jobs.tasks.execute_refund", str(refund.id))
    make_worker(app).work(burst=True)
    again.refresh()
    assert again.get_status() == JobStatus.FINISHED
    assert len(made) == 2
    assert _refund_lines(db_session) == paid


def _refund_lines(session) -> int:  # type: ignore[no-untyped-def]
    session.expire_all()
    return session.execute(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.entry_type == LedgerEntryType.REFUND_ISSUED)
    ).scalar_one()
