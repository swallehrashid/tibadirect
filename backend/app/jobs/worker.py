"""The worker process: ``python -m app.jobs.worker [queue ...]``.

Runs every job inside an application context and hands the database connection back after each
one. It starts RQ's own scheduler thread too, which is what delivers the delayed retries.
"""

import sys
from typing import Any

from flask import Flask
from rq import Queue
from rq.job import Job
from rq.worker import SimpleWorker

from app.extensions import db
from app.jobs.queue import QUEUE_NAMES, get_connection, get_queue


class AppWorker(SimpleWorker):
    """A worker that runs each job in the application context, in its own process.

    ``SimpleWorker`` does not fork per job, so the SQLAlchemy pool is never shared across a fork.
    """

    def __init__(self, *args: Any, app: Flask, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.app = app

    def perform_job(self, job: Job, queue: Queue) -> bool:
        with self.app.app_context():
            try:
                return super().perform_job(job, queue)
            finally:
                db.session.remove()


def make_worker(app: Flask, names: tuple[str, ...] = QUEUE_NAMES) -> AppWorker:
    with app.app_context():
        queues = [get_queue(name) for name in names]
        return AppWorker(queues, connection=get_connection(), app=app)


def main(argv: list[str] | None = None) -> None:
    from app import create_app

    names = tuple(argv if argv is not None else sys.argv[1:]) or QUEUE_NAMES
    unknown = [name for name in names if name not in QUEUE_NAMES]
    if unknown:
        raise SystemExit(f"Unknown queue(s): {', '.join(unknown)}")
    app = create_app()
    make_worker(app, names).work(with_scheduler=True)


if __name__ == "__main__":
    main()
