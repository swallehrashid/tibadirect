"""Private object storage, against moto's in-memory S3."""

import logging
import uuid
from collections.abc import Iterator

import pytest
import requests
from botocore.exceptions import EndpointConnectionError
from flask import Flask

from app.services import storage_service
from app.services.storage_service import StorageError


@pytest.fixture
def ctx(app: Flask, s3: None) -> Iterator[Flask]:
    with app.app_context():
        yield app


def test_the_key_is_built_from_ids_and_the_extension_only() -> None:
    case_id, document_id = uuid.uuid4(), uuid.uuid4()
    assert storage_service.document_key(case_id, document_id, "pdf") == (
        f"cases/{case_id}/{document_id}.pdf"
    )
    assert storage_service.document_key(case_id, document_id, ".png").endswith(".png")


def test_an_object_round_trips_with_its_content_type(ctx: Flask) -> None:
    storage_service.put_object("cases/a/b.pdf", b"%PDF-bytes", "application/pdf")
    assert storage_service.get_object("cases/a/b.pdf") == b"%PDF-bytes"
    head = storage_service._client().head_object(
        Bucket=ctx.config["S3_BUCKET"], Key="cases/a/b.pdf"
    )
    assert head["ContentType"] == "application/pdf"


def test_server_side_encryption_is_requested_when_configured(
    ctx: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(ctx.config, "S3_SERVER_SIDE_ENCRYPTION", "AES256")
    storage_service.put_object("cases/a/b.pdf", b"x", "application/pdf")
    head = storage_service._client().head_object(
        Bucket=ctx.config["S3_BUCKET"], Key="cases/a/b.pdf"
    )
    assert head["ServerSideEncryption"] == "AES256"


def test_existence_and_deletion(ctx: Flask) -> None:
    assert storage_service.object_exists("cases/a/b.pdf") is False
    storage_service.put_object("cases/a/b.pdf", b"x", "application/pdf")
    assert storage_service.object_exists("cases/a/b.pdf") is True
    storage_service.delete_object("cases/a/b.pdf")
    assert storage_service.object_exists("cases/a/b.pdf") is False
    storage_service.delete_object("cases/a/b.pdf")  # already gone: not an error


def test_reading_a_missing_object_is_a_storage_error(ctx: Flask) -> None:
    with pytest.raises(StorageError) as raised:
        storage_service.get_object("cases/none/none.pdf")
    assert raised.value.status_code == 503
    assert raised.value.code == "storage_unavailable"


def test_failures_are_logged_without_the_key(ctx: Flask, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR), pytest.raises(StorageError):
        storage_service.get_object("cases/very-identifying-key/x.pdf")
    assert caplog.records
    assert "very-identifying-key" not in caplog.text
    assert any(getattr(r, "action", None) == "get" for r in caplog.records)


def test_delete_prefix_removes_only_that_prefix(ctx: Flask) -> None:
    for key in ("demo/cases/1.pdf", "demo/cases/2.pdf", "cases/real/1.pdf"):
        storage_service.put_object(key, b"x", "application/pdf")
    assert storage_service.delete_prefix("demo/") == 2
    assert storage_service.object_exists("cases/real/1.pdf")
    assert not storage_service.object_exists("demo/cases/1.pdf")
    assert storage_service.delete_prefix("demo/") == 0


def test_delete_prefix_handles_more_than_one_batch(ctx: Flask) -> None:
    for index in range(1005):
        storage_service.put_object(f"demo/many/{index}.bin", b"x", "application/octet-stream")
    assert storage_service.delete_prefix("demo/") == 1005
    assert list(storage_service._keys_under("")) == []


def test_delete_prefix_refuses_an_empty_prefix(ctx: Flask) -> None:
    storage_service.put_object("cases/a/b.pdf", b"x", "application/pdf")
    with pytest.raises(ValueError, match="empty prefix"):
        storage_service.delete_prefix("")
    assert storage_service.object_exists("cases/a/b.pdf")


# --- Signed links ------------------------------------------------------------------------------


def test_a_signed_link_works_for_five_minutes_and_reads_the_file(ctx: Flask) -> None:
    storage_service.put_object("cases/a/b.pdf", b"%PDF-bytes", "application/pdf")
    url = storage_service.signed_url("cases/a/b.pdf")
    assert "X-Amz-Expires=300" in url
    response = requests.get(url, timeout=5)
    assert response.status_code == 200
    assert response.content == b"%PDF-bytes"
    assert response.headers["Content-Disposition"] == "inline"
    assert "b.pdf" not in response.headers["Content-Disposition"]


def test_a_signed_link_never_lasts_longer_than_five_minutes(ctx: Flask) -> None:
    assert "X-Amz-Expires=300" in storage_service.signed_url("k", expires_seconds=86_400)
    assert "X-Amz-Expires=60" in storage_service.signed_url("k", expires_seconds=60)


def test_a_signed_link_without_the_key_is_refused(ctx: Flask) -> None:
    storage_service.put_object("cases/a/b.pdf", b"x", "application/pdf")
    url = storage_service.signed_url("cases/a/b.pdf")
    bare = url.split("?")[0]
    assert requests.get(bare, timeout=5).status_code in {401, 403}


def test_links_point_at_the_public_address_when_one_is_set(
    ctx: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(ctx.config, "S3_PUBLIC_ENDPOINT_URL", "http://localhost:9000")
    ctx.extensions.pop("s3_signing_client", None)
    try:
        url = storage_service.signed_url("cases/a/b.pdf")
        assert url.startswith("http://localhost:9000/")
    finally:
        ctx.extensions.pop("s3_signing_client", None)


# --- The bucket --------------------------------------------------------------------------------


def test_bootstrap_creates_a_private_encrypted_bucket_once(app: Flask, s3: None) -> None:
    with app.app_context():
        # the s3 fixture already created it
        assert storage_service.ensure_bucket() is False
        client = storage_service._client()
        bucket = app.config["S3_BUCKET"]
        block = client.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
        assert all(block.values())
        rules = client.get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]
        assert rules["Rules"][0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"


def test_bootstrap_reports_when_it_created_the_bucket(app: Flask) -> None:
    with app.app_context():
        assert storage_service.ensure_bucket() is True
        assert storage_service.ensure_bucket() is False
        client = storage_service._client()
        client.delete_bucket(Bucket=app.config["S3_BUCKET"])


def test_a_store_without_encryption_support_still_gets_a_bucket(
    app: Flask, s3: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from botocore.exceptions import ClientError

    def unsupported(**kwargs: object) -> None:
        raise ClientError({"Error": {"Code": "NotImplemented", "Message": "no kms"}}, "Put")

    with app.app_context():
        monkeypatch.setattr(storage_service._client(), "put_bucket_encryption", unsupported)
        with caplog.at_level(logging.WARNING):
            assert storage_service.ensure_bucket() is False
        assert any(getattr(r, "setting", None) == "default_encryption" for r in caplog.records)


def test_an_unreachable_store_is_a_storage_error_everywhere(
    app: Flask, s3: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(*args: object, **kwargs: object) -> None:
        raise EndpointConnectionError(endpoint_url="http://nowhere")

    with app.app_context():
        monkeypatch.setattr(storage_service._client(), "_make_api_call", down)
        for call in (
            lambda: storage_service.put_object("k", b"x", "text/plain"),
            lambda: storage_service.get_object("k"),
            lambda: storage_service.object_exists("k"),
            lambda: storage_service.delete_object("k"),
            lambda: storage_service.delete_prefix("demo/"),
            storage_service.ensure_bucket,
        ):
            with pytest.raises(StorageError):
                call()
