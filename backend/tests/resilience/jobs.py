"""Jobs for the restart test. They live in a module the worker process can import by name."""

import time

from app.jobs.queue import get_connection

RUNS = "resilience:runs"
STARTED = "resilience:started"
DONE = "resilience:done"


def long_job(seconds: int) -> str:
    """Slow the first time, quick after that, so a killed run leaves time to be killed in."""
    connection = get_connection()
    runs = connection.incr(RUNS)
    connection.set(STARTED, runs)
    if runs == 1:
        time.sleep(seconds)
    connection.incr(DONE)
    return "done"
