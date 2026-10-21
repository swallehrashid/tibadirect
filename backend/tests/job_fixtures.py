"""Job functions used by the job tests. Workers import them by dotted path."""

from flask import current_app

from app.extensions import db
from app.jobs.queue import job_request_id


def add(a: int, b: int) -> int:
    return a + b


def explode(message: str = "boom") -> None:
    raise RuntimeError(message)


def app_name() -> str:
    return current_app.name


def request_id() -> str | None:
    return job_request_id()


def session_is_usable() -> int:
    return db.session.execute(db.text("SELECT 41 + 1")).scalar_one()
