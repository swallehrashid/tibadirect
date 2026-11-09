"""A worker that dies in the middle of a job loses nothing: another one finishes the job.

A real worker process is started, killed with SIGKILL while a job is running (as the host would
on a deploy or an out-of-memory kill), and a new worker is started. RQ notices the abandoned job
when its time allowance has passed, which the test fast-forwards, and the standard retry policy
runs it again.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from flask import Flask
from rq.job import JobStatus
from rq.registry import StartedJobRegistry

from app.jobs import queue as jobs
from tests.resilience import jobs as fixtures

BACKEND = Path(__file__).resolve().parents[2]
WORKER_CODE = (
    "from app import create_app;"
    "from app.jobs.worker import make_worker;"
    "make_worker(create_app('test')).work(burst=True)"
)


def _start_worker() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", WORKER_CODE],
        cwd=BACKEND,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait(read, timeout: float = 30.0):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


@pytest.mark.slow
def test_a_job_whose_worker_was_killed_is_run_again_by_the_next_worker(
    app: Flask, async_jobs: None
) -> None:
    with app.app_context():
        connection = jobs.get_connection()
        job = jobs.enqueue(jobs.QUEUE_PAYMENTS, f"{fixtures.__name__}.long_job", 120)

        first = _start_worker()
        try:
            _wait(lambda: connection.get(fixtures.STARTED))
            first.kill()  # no chance to clean up, like a deploy or an out-of-memory kill
            first.wait(timeout=10)
        finally:
            if first.poll() is None:
                first.kill()

        queue = jobs.get_queue(jobs.QUEUE_PAYMENTS)
        registry = StartedJobRegistry(queue=queue)
        assert job.id in registry.get_job_ids(), "the dead worker left the job marked as running"
        assert int(connection.get(fixtures.DONE) or 0) == 0

        # The time allowance passes; a worker starting up sweeps the registry before it works.
        registry.cleanup(time.time() + jobs.JOB_TIMEOUT_SECONDS * 2)
        job.refresh()
        assert job.retries_left == jobs.RETRY_MAX - 1
        # The retry policy waits ten seconds before the next try; the scheduler then puts the job
        # back on its queue. Do that step by hand instead of waiting.
        assert job.get_status() == JobStatus.SCHEDULED
        queue.scheduled_job_registry.remove(job)
        queue.enqueue_job(job)

        second = _start_worker()
        try:
            assert second.wait(timeout=60) == 0
        finally:
            if second.poll() is None:
                second.kill()

        job.refresh()
        assert job.get_status() == JobStatus.FINISHED
        assert int(connection.get(fixtures.RUNS)) == 2
        assert int(connection.get(fixtures.DONE)) == 1, "it finished once, not twice"
        assert registry.count == 0
        assert queue.failed_job_registry.count == 0
