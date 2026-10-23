"""The two background jobs behind the webhook route."""

import uuid
from datetime import UTC, datetime, timedelta

from app.jobs import tasks
from app.models.enums import DonationStatus, WebhookStatus
from app.models.integration import WebhookEvent
from tests.donation_helpers import live_case, order_for
from tests.webhook_helpers import make_event


def stored_event(session, event: dict, *, age_minutes: int = 0) -> WebhookEvent:  # type: ignore[no-untyped-def]
    row = WebhookEvent(
        paypal_event_id=event["id"],
        event_type=event["event_type"],
        payload=event,
        signature_verified=True,
        received_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
    )
    session.add(row)
    session.commit()
    return row


def test_the_process_job_takes_an_id_and_returns_the_outcome(db_session, paypal) -> None:  # type: ignore[no-untyped-def]
    donation = order_for(db_session, live_case())
    row = stored_event(
        db_session,
        make_event("PAYMENT.CAPTURE.DENIED", {"id": "C-1", "custom_id": str(donation.id)}),
    )

    assert tasks.process_webhook_event(str(row.id)) == "processed"

    db_session.refresh(donation)
    assert donation.status == DonationStatus.DENIED


def test_the_process_job_for_an_unknown_event_does_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    assert tasks.process_webhook_event(uuid.uuid4()) is None


def test_the_sweep_job_queues_old_unprocessed_events_and_reports_how_many(  # type: ignore[no-untyped-def]
    db_session, paypal
) -> None:
    old = stored_event(
        db_session, make_event("PAYMENT.CAPTURE.DENIED", {"id": "C-1"}), age_minutes=10
    )
    stored_event(db_session, make_event("PAYMENT.CAPTURE.DENIED", {"id": "C-2"}), age_minutes=0)

    assert tasks.sweep_webhook_events() == 1

    db_session.refresh(old)
    assert old.status == WebhookStatus.IGNORED  # the queue is inline in tests, so it ran
