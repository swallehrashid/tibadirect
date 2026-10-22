"""What the admin console shows and does about background jobs."""

import re
from datetime import UTC, datetime
from typing import Any

from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.errors import Conflict, NotFound
from app.extensions import get_redis
from app.jobs import queue as jobs
from app.jobs.tasks import HEARTBEAT_KEY
from app.logging import scrub

# One heartbeat is queued every five minutes; three missed ones mean something is not running.
HEARTBEAT_HEALTHY_SECONDS = 900
FAILED_JOBS_SHOWN = 50
MAX_ERROR_CHARS = 500
_JOB_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _last_error_line(job: Job) -> str:
    """The last line of the traceback, scrubbed and cut short. Never the arguments."""
    result = job.latest_result()
    text = (result.exc_string if result is not None else None) or ""
    last = next((line for line in reversed(text.strip().splitlines()) if line.strip()), "")
    return scrub(last)[:MAX_ERROR_CHARS]


def _failed_job(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "queue": job.origin,
        "function": job.func_name,
        "enqueued_at": job.enqueued_at,
        "failed_at": job.ended_at,
        "retries_left": job.retries_left or 0,
        "request_id": job.meta.get("request_id"),
        "error": _last_error_line(job),
    }


def _scheduler_status() -> dict[str, Any]:
    raw = get_redis().get(HEARTBEAT_KEY)
    if raw is None:
        return {"last_heartbeat": None, "age_seconds": None, "healthy": False}
    last = datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else raw)
    age = int((datetime.now(UTC) - last).total_seconds())
    return {"last_heartbeat": last, "age_seconds": age, "healthy": age <= HEARTBEAT_HEALTHY_SECONDS}


def overview() -> dict[str, Any]:
    """Queue sizes, the newest failed jobs and whether the scheduler is alive."""
    queues, failed = [], []
    for queue in jobs.all_queues():
        registry = queue.failed_job_registry
        queues.append(
            {
                "name": queue.name,
                "queued": queue.count,
                "started": queue.started_job_registry.count,
                "scheduled": queue.scheduled_job_registry.count,
                "failed": registry.count,
            }
        )
        for job in Job.fetch_many(registry.get_job_ids(), connection=jobs.get_connection()):
            if job is not None:
                failed.append(_failed_job(job))
    failed.sort(
        key=lambda item: item["failed_at"] or datetime.min.replace(tzinfo=UTC), reverse=True
    )
    return {
        "queues": queues,
        "failed_jobs": failed[:FAILED_JOBS_SHOWN],
        "scheduler": _scheduler_status(),
    }


def retry_failed(job_id: str) -> Job:
    """Put a failed job back on its queue. Raises 404 if unknown, 409 if it did not fail."""
    if not _JOB_ID.match(job_id):
        raise NotFound("Job not found.", code="job_not_found")
    try:
        job = Job.fetch(job_id, connection=jobs.get_connection())
    except NoSuchJobError as exc:
        raise NotFound("Job not found.", code="job_not_found") from exc
    queue = jobs.get_queue(job.origin)
    if job_id not in queue.failed_job_registry.get_job_ids():
        raise Conflict("Only a failed job can be retried.", code="job_not_failed")
    queue.failed_job_registry.requeue(job_id)
    return Job.fetch(job_id, connection=jobs.get_connection())
