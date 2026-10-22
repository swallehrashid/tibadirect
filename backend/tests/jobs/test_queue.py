"""Queues, the enqueue helper and the retry policy."""

import pytest
from flask import Flask, g
from rq.job import JobStatus

from app.jobs import queue as jobs
from app.jobs.worker import make_worker

FIXTURES = "tests.job_fixtures"


def test_there_are_three_named_queues(app: Flask) -> None:
    assert jobs.QUEUE_NAMES == ("triage", "payments", "default")
    with app.app_context():
        assert [q.name for q in jobs.all_queues()] == ["triage", "payments", "default"]


def test_an_unknown_queue_is_refused(app: Flask) -> None:
    with app.app_context(), pytest.raises(ValueError, match="Unknown queue"):
        jobs.get_queue("urgent")


def test_a_job_waits_on_its_queue_with_the_retry_policy(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        job = jobs.enqueue(jobs.QUEUE_TRIAGE, f"{FIXTURES}.add", 2, 3)
        assert job.origin == "triage"
        assert job.get_status() == JobStatus.QUEUED
        assert job.retries_left == 3
        assert job.retry_intervals == [10, 60, 300]
        assert jobs.get_queue("triage").count == 1
        assert jobs.get_queue("payments").count == 0


def test_the_request_id_travels_with_the_job(app: Flask, async_jobs: None) -> None:
    with app.test_request_context("/"):
        g.request_id = "req-from-the-click"
        job = jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.request_id")
    assert job.meta["request_id"] == "req-from-the-click"


def test_a_job_outside_a_request_has_no_request_id(app: Flask, async_jobs: None) -> None:
    with app.app_context():
        assert jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.request_id").meta == {}


def test_a_job_can_read_the_request_id_it_was_enqueued_with(app: Flask, async_jobs: None) -> None:
    with app.test_request_context("/"):
        g.request_id = "req-12345678"
        job = jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.request_id")
    with app.app_context():
        make_worker(app).work(burst=True)
        job.refresh()
        assert job.return_value() == "req-12345678"


def test_inline_mode_runs_the_job_at_once(app: Flask, redis_clean: None) -> None:
    with app.app_context():
        job = jobs.enqueue(jobs.QUEUE_DEFAULT, f"{FIXTURES}.add", 2, 3)
        assert job.get_status() == JobStatus.FINISHED
        assert job.return_value() == 5
