"""Triage of one bill: results are stored once, and the case moves on when every bill is done."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai import pipeline
from app.ai.pipeline import TriageOutcome
from app.extensions import db
from app.jobs import tasks
from app.models.case import TriageResult
from app.models.enums import (
    ActorType,
    CaseStatus,
    DocumentKind,
    DocumentStatus,
    RiskBand,
    TriageRoute,
)
from app.models.platform import AuditLog
from app.services import document_service
from tests.factories import CaseDocumentFactory, CaseFactory


def _waiting_case(documents: int = 1, **fields):  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.PENDING_TRIAGE, **fields)
    docs = [CaseDocumentFactory(case=case) for _ in range(documents)]
    # A job starts from saved data. Committing here also keeps a rollback inside the code under
    # test from taking the setup with it.
    db.session.commit()
    return case, docs


def test_the_stub_returns_a_neutral_result_for_the_hospital(db_session) -> None:  # type: ignore[no-untyped-def]
    outcome = pipeline.analyse(CaseDocumentFactory())
    assert outcome.route == TriageRoute.HOSPITAL_CONFIRMATION
    assert (outcome.risk_score, outcome.risk_band) == (0, RiskBand.LOW)
    assert outcome.reasons_en == ["Automatic checks pending"]
    assert outcome.reasons_sw
    assert outcome.used_llm is False
    assert outcome.model_versions == {"pipeline": "stub-v0"}


def test_a_document_is_triaged_and_the_case_moves_to_the_hospital(db_session) -> None:  # type: ignore[no-untyped-def]
    case, [document] = _waiting_case()
    result = pipeline.process_document(document.id)
    assert isinstance(result, TriageResult)
    assert (result.case_id, result.document_id) == (case.id, document.id)
    assert result.route == TriageRoute.HOSPITAL_CONFIRMATION
    assert result.duration_ms >= 0
    assert document.status == DocumentStatus.TRIAGED
    assert case.status == CaseStatus.PENDING_CONFIRMATION
    assert case.trust_level.value == "pending_confirmation"


def test_the_move_is_audited_as_the_system_with_the_enqueuing_request_id(db_session) -> None:  # type: ignore[no-untyped-def]
    _, [document] = _waiting_case()
    pipeline.process_document(document.id, request_id="req-from-the-click")
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "case.transitioned")
    ).scalar_one()
    assert entry.actor_type == ActorType.SYSTEM
    assert entry.request_id == "req-from-the-click"
    assert entry.details == {
        "from": "pending_triage",
        "to": "pending_confirmation",
        "reason": "triage_complete",
    }


def test_running_twice_stores_one_result_and_moves_the_case_once(db_session) -> None:  # type: ignore[no-untyped-def]
    case, [document] = _waiting_case()
    first = pipeline.process_document(document.id)
    second = pipeline.process_document(document.id)
    assert second.id == first.id
    assert len(db_session.execute(select(TriageResult)).scalars().all()) == 1
    assert case.status == CaseStatus.PENDING_CONFIRMATION
    moves = (
        db_session.execute(select(AuditLog).where(AuditLog.action == "case.transitioned"))
        .scalars()
        .all()
    )
    assert len(moves) == 1


def test_the_case_waits_until_every_bill_has_a_result(db_session) -> None:  # type: ignore[no-untyped-def]
    case, [first, second] = _waiting_case(documents=2)
    pipeline.process_document(first.id)
    assert case.status == CaseStatus.PENDING_TRIAGE
    pipeline.process_document(second.id)
    assert case.status == CaseStatus.PENDING_CONFIRMATION


def test_a_photo_does_not_hold_the_case_back(db_session) -> None:  # type: ignore[no-untyped-def]
    case, [bill] = _waiting_case()
    CaseDocumentFactory(case=case, kind=DocumentKind.PHOTO)
    pipeline.process_document(bill.id)
    assert case.status == CaseStatus.PENDING_CONFIRMATION


def test_one_bill_that_needs_a_person_sends_the_whole_case_to_review(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    case, [first, second] = _waiting_case(documents=2)
    routes = iter([TriageRoute.HOSPITAL_CONFIRMATION, TriageRoute.MANUAL_REVIEW])
    monkeypatch.setattr(
        pipeline,
        "analyse",
        lambda document: TriageOutcome(route=next(routes), reasons_en=["x"], reasons_sw=["y"]),
    )
    pipeline.process_document(first.id)
    pipeline.process_document(second.id)
    assert case.status == CaseStatus.IN_REVIEW
    assert case.trust_level.value == "unverified"


def test_a_case_that_moved_on_is_left_alone(db_session) -> None:  # type: ignore[no-untyped-def]
    case, [document] = _waiting_case()
    case.status = CaseStatus.CANCELLED
    db_session.flush()
    assert pipeline.process_document(document.id) is None
    assert document.status == DocumentStatus.UPLOADED
    assert db_session.execute(select(TriageResult)).first() is None
    assert case.status == CaseStatus.CANCELLED


def test_an_unknown_document_is_ignored(db_session) -> None:  # type: ignore[no-untyped-def]
    assert pipeline.process_document(uuid.uuid4()) is None


def test_a_crash_marks_the_document_failed_and_lets_the_queue_retry(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    case, [document] = _waiting_case()

    def explode(doc: object) -> None:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(pipeline, "analyse", explode)
    with pytest.raises(RuntimeError, match="model unavailable"):
        pipeline.process_document(document.id)
    db_session.expire_all()
    assert document.status == DocumentStatus.FAILED
    assert case.status == CaseStatus.PENDING_TRIAGE
    assert db_session.execute(select(TriageResult)).first() is None


def test_a_failed_document_is_checked_again_on_retry(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    case, [document] = _waiting_case()
    calls = {"n": 0}
    real = pipeline.analyse

    def flaky(doc):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first try fails")
        return real(doc)

    monkeypatch.setattr(pipeline, "analyse", flaky)
    with pytest.raises(RuntimeError):
        pipeline.process_document(document.id)
    pipeline.process_document(document.id)
    db_session.expire_all()
    assert document.status == DocumentStatus.TRIAGED
    assert case.status == CaseStatus.PENDING_CONFIRMATION


# --- The job and the sweep ---------------------------------------------------------------------


def test_the_job_takes_an_id_and_returns_the_result_id(db_session) -> None:  # type: ignore[no-untyped-def]
    _, [document] = _waiting_case()
    result_id = tasks.triage_document(str(document.id))
    assert result_id == str(db_session.execute(select(TriageResult.id)).scalar_one())
    assert tasks.triage_document(uuid.uuid4()) is None


def _stale(case) -> None:  # type: ignore[no-untyped-def]
    case.submitted_at = datetime.now(UTC) - timedelta(minutes=10)


def test_the_sweep_finds_old_bills_that_no_worker_started(db_session) -> None:  # type: ignore[no-untyped-def]
    case, [document] = _waiting_case()
    _stale(case)
    db_session.flush()
    assert document_service.documents_waiting_for_triage(db_session) == [document]


def test_the_sweep_leaves_recent_started_and_finished_work_alone(db_session) -> None:  # type: ignore[no-untyped-def]
    fresh, _ = _waiting_case()
    fresh.submitted_at = datetime.now(UTC) - timedelta(seconds=30)
    started, [processing] = _waiting_case()
    _stale(started)
    processing.status = DocumentStatus.PROCESSING
    done, [triaged] = _waiting_case()
    _stale(done)
    triaged.status = DocumentStatus.TRIAGED
    draft = CaseFactory(status=CaseStatus.DRAFT)
    CaseDocumentFactory(case=draft)
    moved_on = CaseFactory(status=CaseStatus.PENDING_CONFIRMATION)
    _stale(moved_on)
    CaseDocumentFactory(case=moved_on)
    db_session.flush()
    assert document_service.documents_waiting_for_triage(db_session) == []


def test_the_sweep_job_queues_what_it_found(db_session, async_jobs: None, app) -> None:  # type: ignore[no-untyped-def]
    from app.jobs import queue as jobs

    case, _ = _waiting_case(documents=2)
    _stale(case)
    db_session.flush()
    assert tasks.sweep_pending_triage() == 2
    with app.app_context():
        assert jobs.get_queue(jobs.QUEUE_TRIAGE).count == 2


def test_the_sweep_is_scheduled_every_five_minutes() -> None:
    from app.jobs import scheduler

    job = next(j for j in scheduler.PERIODIC_JOBS if j.name == "sweep_pending_triage")
    assert (job.queue, job.every_seconds) == ("triage", 300)
