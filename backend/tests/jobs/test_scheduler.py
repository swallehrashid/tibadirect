"""The scheduler: due jobs enqueue once per period and only one scheduler runs."""

from collections.abc import Callable

import pytest
from flask import Flask

from app.extensions import get_redis
from app.jobs import queue as jobs
from app.jobs import scheduler
from app.jobs.scheduler import PeriodicJob
from app.jobs.worker import make_worker

# Periodic jobs the real schedule puts on the default queue (each is queued once on first run).
DEFAULT_QUEUE_JOBS = sum(1 for job in scheduler.PERIODIC_JOBS if job.queue == jobs.QUEUE_DEFAULT)

SCHEDULE = [
    PeriodicJob("quick", "tests.job_fixtures.add", jobs.QUEUE_DEFAULT, 60),
    PeriodicJob("slow", "tests.job_fixtures.add", jobs.QUEUE_PAYMENTS, 3600),
]


def _counter(limit: int) -> Callable[[], bool]:
    calls = {"n": 0}

    def should_stop() -> bool:
        calls["n"] += 1
        return calls["n"] > limit

    return should_stop


def test_the_heartbeat_is_in_the_real_schedule() -> None:
    assert [job.name for job in scheduler.PERIODIC_JOBS] == [
        "heartbeat",
        "sweep_pending_triage",
        "retention_sweep",
        "refresh_fx_rates",
        "sweep_webhook_events",
    ]


def test_a_job_is_enqueued_once_per_period(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        redis = get_redis()
        assert scheduler.enqueue_due(redis, SCHEDULE) == ["quick", "slow"]
        assert scheduler.enqueue_due(redis, SCHEDULE) == []
        assert jobs.get_queue(jobs.QUEUE_DEFAULT).count == 1
        assert jobs.get_queue(jobs.QUEUE_PAYMENTS).count == 1


def test_a_job_is_enqueued_again_when_its_period_has_passed(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        redis = get_redis()
        scheduler.enqueue_due(redis, SCHEDULE)
        redis.delete(scheduler.DUE_KEY.format(name="quick"))  # what the expiry does
        assert scheduler.enqueue_due(redis, SCHEDULE) == ["quick"]
        assert jobs.get_queue(jobs.QUEUE_DEFAULT).count == 2


def test_the_due_clock_expires_by_itself(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        redis = get_redis()
        scheduler.enqueue_due(redis, SCHEDULE)
        assert 0 < redis.ttl(scheduler.DUE_KEY.format(name="quick")) <= 60
        assert 60 < redis.ttl(scheduler.DUE_KEY.format(name="slow")) <= 3600


def test_the_scheduler_takes_the_lock_enqueues_and_lets_go(app: Flask, async_jobs: None) -> None:
    ticks: list[float] = []
    scheduler.run(app, tick_seconds=5, sleep=ticks.append, should_stop=_counter(3))
    with app.app_context():
        redis = get_redis()
        assert ticks == [5, 5]
        assert (
            jobs.get_queue(jobs.QUEUE_DEFAULT).count == DEFAULT_QUEUE_JOBS
        )  # each periodic job once
        assert redis.get(scheduler.LOCK_KEY) is None


def test_a_second_scheduler_stands_by_while_one_holds_the_lock(
    app: Flask, async_jobs: None
) -> None:
    with app.app_context():
        holder = get_redis().lock(scheduler.LOCK_KEY, timeout=60)
        assert holder.acquire(blocking=False)
    waits: list[float] = []
    scheduler.run(app, tick_seconds=7, sleep=waits.append, should_stop=_counter(3))
    with app.app_context():
        assert waits == [7, 7, 7]
        assert jobs.get_queue(jobs.QUEUE_DEFAULT).count == 0
        assert get_redis().get(scheduler.LOCK_KEY) is not None  # still the holder's
        holder.release()


def test_a_waiting_scheduler_takes_over_when_the_lock_is_released(
    app: Flask, async_jobs: None
) -> None:
    with app.app_context():
        holder = get_redis().lock(scheduler.LOCK_KEY, timeout=60)
        holder.acquire(blocking=False)

    def release_then_wait(_seconds: float) -> None:
        if holder.owned():
            holder.release()

    scheduler.run(app, tick_seconds=1, sleep=release_then_wait, should_stop=_counter(4))
    with app.app_context():
        assert jobs.get_queue(jobs.QUEUE_DEFAULT).count == DEFAULT_QUEUE_JOBS


def test_losing_the_lock_makes_the_scheduler_stand_by_instead_of_crashing(
    app: Flask, async_jobs: None
) -> None:
    steps = {"n": 0}

    def steal_the_lock(_seconds: float) -> None:
        steps["n"] += 1
        if steps["n"] == 1:
            get_redis().delete(scheduler.LOCK_KEY)

    scheduler.run(app, tick_seconds=1, sleep=steal_the_lock, should_stop=_counter(5))
    assert steps["n"] >= 2


def test_the_heartbeat_runs_end_to_end(app: Flask, async_jobs: None) -> None:
    from app.services import job_service

    with app.app_context():
        assert job_service.overview()["scheduler"]["healthy"] is False
        scheduler.enqueue_due(get_redis())
        make_worker(app).work(burst=True)
        status = job_service.overview()["scheduler"]
        assert status["healthy"] is True
        assert status["age_seconds"] < 5


@pytest.mark.parametrize("job", scheduler.PERIODIC_JOBS, ids=lambda job: job.name)
def test_every_scheduled_job_points_at_a_real_function_and_queue(job: PeriodicJob) -> None:
    import importlib

    module, _, name = job.func.rpartition(".")
    assert callable(getattr(importlib.import_module(module), name))
    assert job.queue in jobs.QUEUE_NAMES
    assert job.every_seconds > 0
