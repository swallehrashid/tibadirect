"""Private object storage for uploaded bills (MinIO locally, R2 or S3 in production).

Objects are never public. A browser reads one only through a signed link that works for five
minutes. Keys are built from IDs (``cases/<case_id>/<document_id>.<ext>``), so a file name, which
may identify a patient, is never stored.
"""

import logging
import uuid
from collections.abc import Iterator
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from flask import current_app

from app.errors import AppError

logger = logging.getLogger(__name__)

SIGNED_URL_SECONDS = 300
DEMO_PREFIX = "demo/"
_CLIENT_KEY = "s3_client"
_SIGNING_CLIENT_KEY = "s3_signing_client"


class StorageError(AppError):
    """The object store did not do what was asked."""

    status_code = 503
    code = "storage_unavailable"
    message = "File storage is not available right now. Please try again."


def document_key(case_id: uuid.UUID, document_id: uuid.UUID, extension: str) -> str:
    """The object key for a bill: IDs and the file extension, nothing from the original name."""
    return f"cases/{case_id}/{document_id}.{extension.lstrip('.')}"


def _make_client(endpoint_url: str | None) -> BaseClient:
    config = current_app.config
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or None,
        aws_access_key_id=config["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=config["S3_SECRET_ACCESS_KEY"],
        region_name=config["S3_REGION"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _client() -> BaseClient:
    client = current_app.extensions.get(_CLIENT_KEY)
    if client is None:
        client = _make_client(current_app.config["S3_ENDPOINT_URL"])
        current_app.extensions[_CLIENT_KEY] = client
    return client


def _signing_client() -> BaseClient:
    """A client whose links point at the address browsers use (it never makes a request)."""
    public = current_app.config["S3_PUBLIC_ENDPOINT_URL"]
    if not public:
        return _client()
    client = current_app.extensions.get(_SIGNING_CLIENT_KEY)
    if client is None:
        client = _make_client(public)
        current_app.extensions[_SIGNING_CLIENT_KEY] = client
    return client


def _bucket() -> str:
    return current_app.config["S3_BUCKET"]


def _fail(action: str, exc: Exception) -> StorageError:
    # Log what failed, never the key or the message: either can carry identifiers.
    reason = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
    logger.error("object storage failed", extra={"action": action, "reason": reason})
    return StorageError()


def put_object(key: str, data: bytes, content_type: str) -> None:
    """Store ``data`` under ``key``, encrypted at rest when ``S3_SERVER_SIDE_ENCRYPTION`` is set."""
    extra: dict[str, Any] = {}
    if encryption := current_app.config["S3_SERVER_SIDE_ENCRYPTION"]:
        extra["ServerSideEncryption"] = encryption
    try:
        _client().put_object(
            Bucket=_bucket(), Key=key, Body=data, ContentType=content_type, **extra
        )
    except (ClientError, BotoCoreError) as exc:
        raise _fail("put", exc) from exc


def get_object(key: str) -> bytes:
    """Read an object. Raises ``StorageError`` if it is missing or the store is down."""
    try:
        return _client().get_object(Bucket=_bucket(), Key=key)["Body"].read()  # type: ignore[no-any-return]
    except (ClientError, BotoCoreError) as exc:
        raise _fail("get", exc) from exc


def object_exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise _fail("head", exc) from exc
    except BotoCoreError as exc:
        raise _fail("head", exc) from exc
    return True


def delete_object(key: str) -> None:
    """Delete one object. Deleting one that is already gone is not an error."""
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
    except (ClientError, BotoCoreError) as exc:
        raise _fail("delete", exc) from exc


def _keys_under(prefix: str) -> Iterator[str]:
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for item in page.get("Contents", []):
            yield item["Key"]


def delete_prefix(prefix: str) -> int:
    """Delete every object whose key starts with ``prefix`` and return how many."""
    if not prefix:
        raise ValueError("Refusing to delete with an empty prefix")
    removed = 0
    try:
        batch: list[dict[str, str]] = []
        for key in _keys_under(prefix):
            batch.append({"Key": key})
            if len(batch) == 1000:
                _client().delete_objects(Bucket=_bucket(), Delete={"Objects": batch})
                removed += len(batch)
                batch = []
        if batch:
            _client().delete_objects(Bucket=_bucket(), Delete={"Objects": batch})
            removed += len(batch)
    except (ClientError, BotoCoreError) as exc:
        raise _fail("delete_prefix", exc) from exc
    return removed


def signed_url(key: str, *, expires_seconds: int = SIGNED_URL_SECONDS) -> str:
    """A link that lets a browser read ``key`` for five minutes, shown inline, with no file name."""
    try:
        return _signing_client().generate_presigned_url(  # type: ignore[no-any-return]
            "get_object",
            Params={
                "Bucket": _bucket(),
                "Key": key,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=min(expires_seconds, SIGNED_URL_SECONDS),
        )
    except (ClientError, BotoCoreError) as exc:
        raise _fail("sign", exc) from exc


def ensure_bucket() -> bool:
    """Create the bucket if it is missing and make sure it is private. True if it was created.

    Encryption and the public-access block are best effort: MinIO has no key service locally and
    refuses the former, and R2 manages both itself.
    """
    created = False
    client = _client()
    try:
        client.head_bucket(Bucket=_bucket())
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket", "NotFound"}:
            raise _fail("head_bucket", exc) from exc
        try:
            client.create_bucket(Bucket=_bucket())
        except (ClientError, BotoCoreError) as create_exc:
            raise _fail("create_bucket", create_exc) from create_exc
        created = True
    except BotoCoreError as exc:
        raise _fail("head_bucket", exc) from exc

    for action, call in (
        (
            "public_access_block",
            lambda: client.put_public_access_block(
                Bucket=_bucket(),
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            ),
        ),
        (
            "default_encryption",
            lambda: client.put_bucket_encryption(
                Bucket=_bucket(),
                ServerSideEncryptionConfiguration={
                    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
                },
            ),
        ),
    ):
        try:
            call()
        except (ClientError, BotoCoreError) as exc:
            logger.warning(
                "bucket setting skipped",
                extra={"setting": action, "reason": str(type(exc).__name__)},
            )
    return created
