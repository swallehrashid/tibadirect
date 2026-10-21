"""The admin job endpoints: queue overview, failed jobs and retry."""

from collections.abc import Callable

from flask import Flask
from sqlalchemy import select

from app.extensions import db
from app.jobs import queue as jobs
from app.jobs.worker import make_worker
from app.models.enums import UserRole
from app.models.platform import AuditLog
from tests.browser import Browser
from tests.factories import UserFactory

BrowserFor = Callable[..., Browser]
FIXTURES = "tests.job_fixtures"


def _admin(browser_for: BrowserFor) -> Browser:
    return browser_for(UserFactory(role=UserRole.ADMIN))


def _fail_for_good(app: Flask, message: str = "boom") -> str:
    """Run a job that fails with no retries left and return its ID."""
    with app.app_context():
        queue = jobs.get_queue(jobs.QUEUE_PAYMENTS)
        job = queue.enqueue_call(
            f"{FIXTURES}.explode",
            kwargs={"message": message},
            failure_ttl=600,
            meta={"request_id": "req-abc12345"},
        )
        make_worker(app, (jobs.QUEUE_PAYMENTS,)).work(burst=True)
        return job.id


def test_only_admins_see_jobs(browser: Browser, browser_for: BrowserFor) -> None:
    assert browser.get("/api/admin/jobs").status_code == 401
    assert browser_for(UserFactory()).get("/api/admin/jobs").status_code == 403
    assert browser.post("/api/admin/jobs/abcdefgh-1/retry").status_code == 401


def test_an_idle_system_shows_three_empty_queues_and_no_scheduler(
    app: Flask, async_jobs: None, browser_for: BrowserFor
) -> None:
    body = _admin(browser_for).get("/api/admin/jobs").get_json()
    assert [(q["name"], q["queued"], q["failed"]) for q in body["queues"]] == [
        ("triage", 0, 0),
        ("payments", 0, 0),
        ("default", 0, 0),
    ]
    assert body["failed_jobs"] == []
    assert body["scheduler"] == {"last_heartbeat": None, "age_seconds": None, "healthy": False}


def test_queue_lengths_count_waiting_jobs(
    app: Flask, async_jobs: None, browser_for: BrowserFor
) -> None:
    with app.app_context():
        jobs.enqueue(jobs.QUEUE_TRIAGE, f"{FIXTURES}.add", 1, 1)
        jobs.enqueue(jobs.QUEUE_TRIAGE, f"{FIXTURES}.add", 1, 1)
    queues = {q["name"]: q for q in _admin(browser_for).get("/api/admin/jobs").get_json()["queues"]}
    assert queues["triage"]["queued"] == 2


def test_a_failed_job_is_listed_without_its_arguments_or_personal_data(
    app: Flask, async_jobs: None, browser_for: BrowserFor
) -> None:
    job_id = _fail_for_good(app, "could not email jane.doe@example.test")
    body = _admin(browser_for).get("/api/admin/jobs").get_json()
    [failed] = body["failed_jobs"]
    assert failed["id"] == job_id
    assert failed["queue"] == "payments"
    assert failed["function"] == f"{FIXTURES}.explode"
    assert failed["request_id"] == "req-abc12345"
    assert failed["error"].startswith("RuntimeError: could not email [email]")
    assert "jane" not in str(body)
    assert "kwargs" not in failed
    assert "args" not in failed
    assert {q["name"]: q["failed"] for q in body["queues"]}["payments"] == 1


def test_retrying_a_failed_job_requeues_it_and_is_audited(
    app: Flask, async_jobs: None, browser_for: BrowserFor
) -> None:
    job_id = _fail_for_good(app)
    admin = UserFactory(role=UserRole.ADMIN)
    browser = browser_for(admin)
    response = browser.post(f"/api/admin/jobs/{job_id}/retry")
    assert response.status_code == 200
    assert response.get_json() == {"id": job_id, "queue": "payments", "status": "queued"}

    queues = {q["name"]: q for q in browser.get("/api/admin/jobs").get_json()["queues"]}
    assert queues["payments"]["queued"] == 1
    assert queues["payments"]["failed"] == 0

    [entry] = db.session.execute(select(AuditLog).where(AuditLog.action == "job.retried")).scalars()
    assert entry.actor_user_id == admin.id
    assert entry.target_type == "job"
    assert entry.details == {
        "job_id": job_id,
        "queue": "payments",
        "function": f"{FIXTURES}.explode",
    }


def test_a_job_that_did_not_fail_cannot_be_retried(
    app: Flask, async_jobs: None, browser_for: BrowserFor
) -> None:
    with app.app_context():
        waiting = jobs.enqueue(jobs.QUEUE_TRIAGE, f"{FIXTURES}.add", 1, 1)
    response = _admin(browser_for).post(f"/api/admin/jobs/{waiting.id}/retry")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "job_not_failed"


def test_an_unknown_or_malformed_job_id_is_not_found(
    app: Flask, async_jobs: None, browser_for: BrowserFor
) -> None:
    browser = _admin(browser_for)
    for job_id in ("00000000-0000-0000-0000-000000000000", "no", "x" * 80, "bad id!"):
        response = browser.post(f"/api/admin/jobs/{job_id}/retry")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] in {"job_not_found", "not_found"}


def test_the_job_routes_are_documented_with_admin_security(client) -> None:  # type: ignore[no-untyped-def]
    spec = client.get("/api/openapi.json").get_json()
    assert spec["paths"]["/api/admin/jobs"]["get"]["security"] == [{"sessionCookie": []}]
    assert "409" in spec["paths"]["/api/admin/jobs/{job_id}/retry"]["post"]["responses"]
