"""Original bill files are deleted 24 hours after confirmation (or cancellation)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.jobs import tasks
from app.models.enums import ActorType, CaseStatus, ConfirmationStatus
from app.models.platform import AuditLog
from app.services import document_service, storage_service
from tests.factories import CaseDocumentFactory, CaseFactory, ConfirmationFactory


@pytest.fixture
def store(app, s3, db_session):  # type: ignore[no-untyped-def]
    return app


def _held(case, content: bytes = b"%PDF-bytes"):  # type: ignore[no-untyped-def]
    key = storage_service.document_key(case.id, __import__("uuid").uuid4(), "pdf")
    storage_service.put_object(key, content, "application/pdf")
    document = CaseDocumentFactory(case=case, storage_key=key)
    return document, key


def _confirmed_case(hours_ago: float):  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    ConfirmationFactory(case=case, finalized_at=datetime.now(UTC) - timedelta(hours=hours_ago))
    return case


def test_a_file_is_deleted_a_day_after_confirmation(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _confirmed_case(25)
    document, key = _held(case)
    assert document_service.sweep_raw_documents(db_session) == 1
    assert document.storage_key is None
    assert document.raw_deleted_at is not None
    assert storage_service.object_exists(key) is False


def test_the_record_of_what_was_checked_is_kept(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _confirmed_case(30)
    document, _ = _held(case)
    document.redacted_text = "Total KES [AMOUNT]"
    sha = document.sha256
    document_service.sweep_raw_documents(db_session)
    assert (document.sha256, document.redacted_text) == (sha, "Total KES [AMOUNT]")


def test_a_file_is_kept_until_the_retention_period_has_passed(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _confirmed_case(23)
    document, key = _held(case)
    assert document_service.sweep_raw_documents(db_session) == 0
    assert document.raw_deleted_at is None
    assert storage_service.object_exists(key)


def test_the_period_follows_the_setting(store, db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(store.config, "RAW_DOC_RETENTION_HOURS", 1)
    case = _confirmed_case(2)
    document, _ = _held(case)
    assert document_service.sweep_raw_documents(db_session) == 1
    assert document.raw_deleted_at is not None


def test_an_unconfirmed_case_keeps_its_file_however_old(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.PENDING_CONFIRMATION)
    case.created_at = datetime.now(UTC) - timedelta(days=30)
    document, key = _held(case)
    assert document_service.sweep_raw_documents(db_session) == 0
    assert storage_service.object_exists(key)


def test_a_queried_or_awaiting_bill_does_not_start_the_clock(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.IN_REVIEW)
    ConfirmationFactory(
        case=case,
        status=ConfirmationStatus.QUERIED,
        amount_kes_minor=None,
        invoice_number=None,
        finalized_at=datetime.now(UTC) - timedelta(days=3),
    )
    _held(case)
    assert document_service.sweep_raw_documents(db_session) == 0


def test_a_cancelled_case_loses_its_file_a_day_after_it_was_cancelled(store, db_session) -> None:  # type: ignore[no-untyped-def]
    fresh = CaseFactory(status=CaseStatus.CANCELLED)
    old = CaseFactory(status=CaseStatus.CANCELLED)
    old.updated_at = datetime.now(UTC) - timedelta(hours=30)
    kept, kept_key = _held(fresh)
    removed, removed_key = _held(old)
    assert document_service.sweep_raw_documents(db_session) == 1
    assert storage_service.object_exists(kept_key)
    assert not storage_service.object_exists(removed_key)
    assert kept.raw_deleted_at is None
    assert removed.raw_deleted_at is not None


def test_already_deleted_files_are_left_alone(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _confirmed_case(48)
    CaseDocumentFactory(case=case, storage_key=None, raw_deleted_at=datetime.now(UTC))
    assert document_service.sweep_raw_documents(db_session) == 0


def test_each_deletion_is_audited_by_the_system(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _confirmed_case(25)
    document, _ = _held(case)
    document_service.sweep_raw_documents(db_session)
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "document.raw_deleted")
    ).scalar_one()
    assert entry.actor_type == ActorType.SYSTEM
    assert entry.details == {"document_id": str(document.id), "reason": "confirmed"}


def test_a_file_that_cannot_be_deleted_is_tried_again_next_time(
    store, db_session, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    case = _confirmed_case(25)
    document, key = _held(case)

    def down(_key: str) -> None:
        raise storage_service.StorageError()

    monkeypatch.setattr(storage_service, "delete_object", down)
    assert document_service.sweep_raw_documents(db_session) == 0
    assert (document.storage_key, document.raw_deleted_at) == (key, None)
    monkeypatch.undo()
    assert document_service.sweep_raw_documents(db_session) == 1


def test_running_the_sweep_twice_deletes_once(store, db_session) -> None:  # type: ignore[no-untyped-def]
    case = _confirmed_case(25)
    _held(case)
    assert document_service.sweep_raw_documents(db_session) == 1
    assert document_service.sweep_raw_documents(db_session) == 0


def test_the_job_runs_the_sweep_and_is_scheduled_hourly(store, db_session) -> None:  # type: ignore[no-untyped-def]
    from app.jobs import scheduler

    case = _confirmed_case(25)
    _held(case)
    assert tasks.retention_sweep() == 1
    job = next(j for j in scheduler.PERIODIC_JOBS if j.name == "retention_sweep")
    assert (job.queue, job.every_seconds) == ("default", 3600)
