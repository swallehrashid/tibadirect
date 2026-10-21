"""The seeded demo bills in the bucket, and the seed commands that manage them."""

import hashlib
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy import select

from app.cli.seed import reset_demo_command, seed_command
from app.cli.storage import storage_group
from app.extensions import db
from app.models.case import CaseDocument
from app.seed import seed_all
from app.services import demo_files, storage_service
from app.services.storage_service import StorageError
from app.synthetic_data import DEFAULT_DATA_DIR


@pytest.fixture
def seeded(app: Flask, db_session, s3) -> None:  # type: ignore[no-untyped-def]
    seed_all(db_session, data_dir=DEFAULT_DATA_DIR)


def _held(session) -> list[CaseDocument]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(CaseDocument).where(
                CaseDocument.storage_key.is_not(None), CaseDocument.raw_deleted_at.is_(None)
            )
        ).scalars()
    )


def test_seeded_documents_use_the_id_based_key_with_an_extension(seeded, db_session) -> None:  # type: ignore[no-untyped-def]
    held = _held(db_session)
    assert held
    for document in held:
        extension = "pdf" if document.mime_type == "application/pdf" else "jpg"
        assert document.storage_key == (f"demo/cases/{document.case_id}/{document.id}.{extension}")


def test_every_held_bill_is_uploaded_with_its_own_bytes(seeded, db_session) -> None:  # type: ignore[no-untyped-def]
    uploaded = demo_files.upload_seed_documents(db_session, DEFAULT_DATA_DIR)
    held = _held(db_session)
    assert uploaded == len(held) > 0
    for document in held:
        data = storage_service.get_object(document.storage_key)
        assert hashlib.sha256(data).hexdigest() == document.sha256
        assert len(data) == document.size_bytes


def test_documents_whose_raw_file_was_deleted_are_not_uploaded(seeded, db_session) -> None:  # type: ignore[no-untyped-def]
    demo_files.upload_seed_documents(db_session, DEFAULT_DATA_DIR)
    deleted = db_session.execute(
        select(CaseDocument).where(CaseDocument.raw_deleted_at.is_not(None))
    ).scalars()
    assert all(document.storage_key is None for document in deleted)


def test_without_the_dataset_nothing_is_uploaded(seeded, db_session, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    assert demo_files.upload_seed_documents(db_session, tmp_path) == 0


def test_clearing_removes_the_demo_prefix_only(seeded, db_session) -> None:  # type: ignore[no-untyped-def]
    demo_files.upload_seed_documents(db_session, DEFAULT_DATA_DIR)
    storage_service.put_object("cases/real/file.pdf", b"x", "application/pdf")
    assert demo_files.clear_demo_files() == len(_held(db_session))
    assert storage_service.object_exists("cases/real/file.pdf")


# --- CLI ---------------------------------------------------------------------------------------


def test_seed_uploads_the_demo_bills(migrated_scratch_app: Flask, s3) -> None:  # type: ignore[no-untyped-def]
    result = migrated_scratch_app.test_cli_runner().invoke(seed_command)
    assert result.exit_code == 0, result.output
    with migrated_scratch_app.app_context():
        keys = list(storage_service._keys_under("demo/"))
        assert keys
        assert len(keys) == len(_held(db.session))
    assert f"Uploaded {len(keys)} demo bill files." in result.output


def test_reset_demo_clears_old_files_before_uploading_new_ones(
    migrated_scratch_app: Flask, s3
) -> None:  # type: ignore[no-untyped-def]
    runner = migrated_scratch_app.test_cli_runner()
    runner.invoke(seed_command)
    with migrated_scratch_app.app_context():
        storage_service.put_object("demo/stale/old.pdf", b"x", "application/pdf")
    result = runner.invoke(reset_demo_command, ["--yes"])
    assert result.exit_code == 0, result.output
    with migrated_scratch_app.app_context():
        keys = list(storage_service._keys_under("demo/"))
        assert "demo/stale/old.pdf" not in keys
        assert len(keys) == len(_held(db.session))


def test_seed_still_works_when_storage_is_down(
    migrated_scratch_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(*args: object, **kwargs: object) -> None:
        raise StorageError()

    monkeypatch.setattr(storage_service, "put_object", down)
    result = migrated_scratch_app.test_cli_runner().invoke(seed_command)
    assert result.exit_code == 0
    assert "demo bill files were not uploaded" in result.output
    with migrated_scratch_app.app_context():
        assert db.session.execute(select(CaseDocument.id)).first() is not None


def test_storage_bootstrap_creates_the_bucket_then_reports_it_exists(app: Flask) -> None:
    runner = app.test_cli_runner()
    first = runner.invoke(storage_group, ["bootstrap"])
    second = runner.invoke(storage_group, ["bootstrap"])
    assert "created and is private" in first.output
    assert "already exists and is private" in second.output
    with app.app_context():
        storage_service._client().delete_bucket(Bucket=app.config["S3_BUCKET"])


def test_storage_bootstrap_reports_an_unreachable_store(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down() -> bool:
        raise StorageError()

    monkeypatch.setattr(storage_service, "ensure_bucket", down)
    result = app.test_cli_runner().invoke(storage_group, ["bootstrap"])
    assert result.exit_code != 0
    assert "Could not reach object storage" in result.output
