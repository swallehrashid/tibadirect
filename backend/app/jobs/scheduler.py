"""The periodic scheduler: ``python -m app.jobs.scheduler``.

A small loop that puts each periodic job on its queue when it is due. A Redis lock makes sure
only one scheduler runs at a time; a second one waits and takes over if the first stops. Each
job also has an expiring "due" key, so even a brief overlap during a hand-over cannot enqueue
the same run twice.
"""

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from flask import Flask
from redis import Redis
from redis.exceptions import LockError

from app.extensions import get_redis
from app.jobs import queue as jobs

logger = logging.getLogger(__name__)

LOCK_KEY = "tibadirect:scheduler:lock"
LOCK_TTL_SECONDS = 60
TICK_SECONDS = 15
DUE_KEY = "tibadirect:scheduler:due:{name}"


@dataclass(frozen=True)
class PeriodicJob:
    name: str
    func: str  # dotted path, imported by the worker
    queue: str
    every_seconds: int


# Later features add their jobs here (FX rates, retention sweep, follow-up holds, ...).
PERIODIC_JOBS: list[PeriodicJob] = [
    PeriodicJob("heartbeat", "app.jobs.tasks.heartbeat", jobs.QUEUE_DEFAULT, 300),
    PeriodicJob(
        "sweep_pending_triage", "app.jobs.tasks.sweep_pending_triage", jobs.QUEUE_TRIAGE, 300
    ),
    PeriodicJob("retention_sweep", "app.jobs.tasks.retention_sweep", jobs.QUEUE_DEFAULT, 3600),
]


def enqueue_due(redis: Redis, schedule: list[PeriodicJob] | None = None) -> list[str]:
    """Enqueue every job whose period has passed and return their names."""
    started = []
    for job in PERIODIC_JOBS if schedule is None else schedule:
        # SET NX with an expiry is the "due" clock: it succeeds once per period, across schedulers.
        if redis.set(DUE_KEY.format(name=job.name), "1", nx=True, ex=job.every_seconds):
            jobs.enqueue(job.queue, job.func)
            started.append(job.name)
    return started


def run(
    app: Flask,
    *,
    tick_seconds: float = TICK_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Hold the lock and enqueue due jobs every ``tick_seconds`` until ``should_stop()``."""
    with app.app_context():
        redis = get_redis()
        while not should_stop():
            lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_SECONDS)
            if not lock.acquire(blocking=False):
                sleep(tick_seconds)  # another scheduler is active; stand by
                continue
            logger.info("scheduler holds the lock")
            try:
                while not should_stop():
                    enqueue_due(redis)
                    lock.extend(LOCK_TTL_SECONDS, replace_ttl=True)
                    sleep(tick_seconds)
            except LockError:
                logger.warning("scheduler lost the lock and is standing by")
            finally:
                with contextlib.suppress(LockError):
                    lock.release()


def main() -> None:
    from app import create_app

    run(create_app())


if __name__ == "__main__":
    main()
