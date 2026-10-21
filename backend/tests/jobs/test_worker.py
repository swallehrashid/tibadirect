"""The worker runs jobs in an application context and handles failure and retry."""

import pytest
from flask import Flask
from rq import Retry
from rq.job import JobStatus

from app.jobs import queue as jobs
from app.jobs import worker
from app.jobs.worker import AppWorker, make_worker

FIXTURES = "tests.job_fixtures"


def test_a_job_runs_inside_the_application_context(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        job = jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.app_name")
        make_worker(app).work(burst=True)
        job.refresh()
        assert job.get_status() == JobStatus.FINISHED
        assert job.return_value() == app.name


def test_a_job_can_use_the_database(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        job = jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.session_is_usable")
        make_worker(app).work(burst=True)
        job.refresh()
        assert job.return_value() == 42


def test_the_worker_reads_every_queue_by_default(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        found = [jobs.enqueue(name, f"{FIXTURES}.add", 1, 1) for name in jobs.QUEUE_NAMES]
        make_worker(app).work(burst=True)
        for job in found:
            job.refresh()
            assert job.get_status() == JobStatus.FINISHED


def test_a_worker_can_be_limited_to_some_queues(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        triage = jobs.enqueue(jobs.QUEUE_TRIAGE, f"{FIXTURES}.add", 1, 1)
        payments = jobs.enqueue(jobs.QUEUE_PAYMENTS, f"{FIXTURES}.add", 1, 1)
        make_worker(app, (jobs.QUEUE_TRIAGE,)).work(burst=True)
        triage.refresh()
        payments.refresh()
        assert triage.get_status() == JobStatus.FINISHED
        assert payments.get_status() == JobStatus.QUEUED


def test_a_failing_job_is_scheduled_for_a_retry_after_ten_seconds(
    app: Flask, async_jobs: None
) -> None:
    with app.app_context():
        job = jobs.enqueue(jobs.QUEUE_PAYMENTS, f"{FIXTURES}.explode")
        make_worker(app).work(burst=True)
        queue = jobs.get_queue(jobs.QUEUE_PAYMENTS)
        assert queue.scheduled_job_registry.get_job_ids() == [job.id]
        assert queue.failed_job_registry.count == 0
        job.refresh()
        assert job.retries_left == 2


def test_a_job_that_keeps_failing_ends_in_the_failed_registry(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        queue = jobs.get_queue(jobs.QUEUE_PAYMENTS)
        job = queue.enqueue_call(f"{FIXTURES}.explode", retry=Retry(max=2), failure_ttl=600)
        make_worker(app).work(burst=True)
        assert queue.failed_job_registry.get_job_ids() == [job.id]
        job.refresh()
        assert job.get_status() == JobStatus.FAILED
        assert job.retries_left == 0


def test_the_database_session_is_released_after_each_job(
    app: Flask, async_jobs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    released = []
    monkeypatch.setattr(worker.db.session, "remove", lambda: released.append(True))
    with app.app_context():
        jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.add", 1, 1)
        jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.explode")
        make_worker(app).work(burst=True)
    assert len(released) >= 2


def test_make_worker_returns_an_app_worker_on_the_given_queues(app: Flask) -> None:
    built = make_worker(app, ("triage", "default"))
    assert isinstance(built, AppWorker)
    assert [q.name for q in built.queues] == ["triage", "default"]


def test_main_rejects_an_unknown_queue_name() -> None:
    with pytest.raises(SystemExit, match="Unknown queue"):
        worker.main(["triage", "urgent"])
