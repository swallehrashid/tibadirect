"""The worker runs jobs in an application context and handles failure and retry."""

import threading

import pytest
from flask import Flask
from rq import Retry
from rq.job import JobStatus

from app.jobs import queue as jobs
from app.jobs import scheduler, worker
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


def test_with_periodic_runs_the_scheduler_in_a_thread_beside_the_worker(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    started = threading.Event()

    class FakeWorker:
        def work(self, **kwargs: object) -> None:
            started.wait(5)
            calls.append(f"work queues={sorted(seen)}")

    seen: list[str] = []
    monkeypatch.setattr("app.create_app", lambda: app)

    def fake_make_worker(_app: Flask, names: tuple[str, ...]) -> FakeWorker:
        seen.extend(names)
        return FakeWorker()

    monkeypatch.setattr(worker, "make_worker", fake_make_worker)
    monkeypatch.setattr(scheduler, "run", lambda _app: (calls.append("periodic"), started.set()))

    worker.main(["--with-periodic", jobs.QUEUE_TRIAGE])
    assert calls == ["periodic", f"work queues={[jobs.QUEUE_TRIAGE]}"]

    calls.clear()
    seen.clear()
    started.clear()
    started.set()  # without the flag nothing else runs; the worker should not wait
    worker.main([jobs.QUEUE_TRIAGE])
    assert calls == [f"work queues={[jobs.QUEUE_TRIAGE]}"]
