"""Job functions. They run inside a worker with an application context.

Each later feature adds its own jobs here and to the schedule in ``scheduler.py`` if periodic.
"""

import logging
import uuid
from datetime import UTC, datetime

from app.ai import pipeline
from app.extensions import db, get_redis
from app.jobs.queue import job_request_id

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "tibadirect:scheduler:heartbeat"
HEARTBEAT_TTL_SECONDS = 600


def heartbeat() -> str:
    """Prove the whole chain works: scheduler, queue and worker. The admin console reads this."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    get_redis().set(HEARTBEAT_KEY, stamp, ex=HEARTBEAT_TTL_SECONDS)
    logger.info("heartbeat job ran")
    return stamp


def triage_document(document_id: str | uuid.UUID) -> str | None:
    """Check one uploaded bill. Takes an ID only, so no personal data reaches the job log."""
    result = pipeline.process_document(uuid.UUID(str(document_id)), request_id=job_request_id())
    return str(result.id) if result is not None else None


def sweep_pending_triage() -> int:
    """Queue triage again for bills a submission left waiting (for example, Redis was down)."""
    from app.services import document_service

    waiting = document_service.documents_waiting_for_triage(db.session)
    return document_service.enqueue_triage(waiting)


def retention_sweep() -> int:
    """Delete original bill files held past the retention period (24 hours after confirmation)."""
    from app.services import document_service

    deleted = document_service.sweep_raw_documents(db.session)
    db.session.commit()
    return deleted


def refresh_fx_rates() -> str:
    """Store the latest USD to KES rate (hourly). Returns where the rate came from."""
    from app.services import fx_service

    rate = fx_service.refresh_rates(db.session)
    db.session.commit()
    return rate.source


def process_webhook_event(event_id: str | uuid.UUID) -> str | None:
    """Handle one stored PayPal event. Takes an ID only; the event holds no payer details."""
    from app.payments import webhooks

    status = webhooks.process_event(db.session, uuid.UUID(str(event_id)))
    return status.value if status is not None else None


def sweep_webhook_events() -> int:
    """Queue processing again for verified events a crash or a Redis outage left waiting."""
    from app.payments import webhooks

    return webhooks.enqueue_stale_events(db.session)
