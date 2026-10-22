"""RQ queues and the one place jobs are enqueued.

Three queues: ``triage`` (document checks), ``payments`` (webhooks, settlements, refunds) and
``default`` (hourly housekeeping). Every job retries three times, after 10 seconds, 1 minute and
5 minutes; a job that still fails stays in the queue's failed registry, where the admin console
lists it and can retry it.
"""

from collections.abc import Callable
from typing import Any

from flask import current_app, g, has_request_context
from redis import Redis
from rq import Queue, Retry, get_current_job
from rq.job import Job

QUEUE_TRIAGE = "triage"
QUEUE_PAYMENTS = "payments"
QUEUE_DEFAULT = "default"
QUEUE_NAMES = (QUEUE_TRIAGE, QUEUE_PAYMENTS, QUEUE_DEFAULT)

RETRY_MAX = 3
RETRY_INTERVALS = (10, 60, 300)

JOB_TIMEOUT_SECONDS = 300
RESULT_TTL_SECONDS = 3600
FAILURE_TTL_SECONDS = 14 * 24 * 3600  # failed jobs stay visible to the admin for two weeks


def get_connection() -> Redis:
    """The Redis connection RQ uses. It must not set a read timeout: workers block on it."""
    client = current_app.extensions.get("rq_redis")
    if client is None:
        client = Redis.from_url(current_app.config["REDIS_URL"], socket_connect_timeout=2)
        current_app.extensions["rq_redis"] = client
    return client


def get_queue(name: str) -> Queue:
    if name not in QUEUE_NAMES:
        raise ValueError(f"Unknown queue {name!r}; use one of {', '.join(QUEUE_NAMES)}")
    return Queue(
        name,
        connection=get_connection(),
        is_async=current_app.config["JOBS_ASYNC"],
        default_timeout=JOB_TIMEOUT_SECONDS,
    )


def enqueue(queue_name: str, func: str | Callable[..., Any], *args: Any, **kwargs: Any) -> Job:
    """Put ``func(*args, **kwargs)`` on a queue with the standard retry policy.

    ``func`` is best given as a dotted path (``"app.jobs.tasks.retention_sweep"``) so the worker
    imports it itself. The ID of the current request travels with the job, so work done in the
    background can be traced back to the click that caused it.
    """
    meta = {"request_id": g.get("request_id")} if has_request_context() else {}
    return get_queue(queue_name).enqueue_call(
        func,
        args=args,
        kwargs=kwargs,
        meta=meta,
        retry=Retry(max=RETRY_MAX, interval=list(RETRY_INTERVALS)),
        result_ttl=RESULT_TTL_SECONDS,
        failure_ttl=FAILURE_TTL_SECONDS,
    )


def job_request_id() -> str | None:
    """Inside a job: the request ID that enqueued it, for ``audit_service.record``."""
    job = get_current_job()
    return job.meta.get("request_id") if job is not None else None


def all_queues() -> list[Queue]:
    return [get_queue(name) for name in QUEUE_NAMES]
