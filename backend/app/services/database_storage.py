"""Demo shortcut: keep bill files in Postgres instead of an object store.

Used when ``STORAGE_BACKEND=database``, so a demo deploys with no bucket. Files are read back
through ``/api/files/<token>``, a link signed with the app's secret that works for five minutes,
the same promise the S3 links make. Not for real patients: a bucket is the production setup.

The table lives outside the models on purpose (it is created on first use and is not part of
the migrations), so turning the shortcut off leaves the schema exactly as it was.
"""

from collections.abc import Iterator

from flask import current_app, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text

from app.extensions import db

_SALT = "database-storage-file"
_CREATE = text(
    "CREATE TABLE IF NOT EXISTS demo_storage_objects ("
    " key TEXT PRIMARY KEY, content_type TEXT NOT NULL, data BYTEA NOT NULL,"
    " created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
)
_READY_KEY = "database_storage_ready"


def _engine():  # type: ignore[no-untyped-def]
    if not current_app.extensions.get(_READY_KEY):
        with db.engine.begin() as connection:
            connection.execute(_CREATE)
        current_app.extensions[_READY_KEY] = True
    return db.engine


def ensure_table() -> bool:
    """Create the table if it is missing. True the first time in this process."""
    created = not current_app.extensions.get(_READY_KEY)
    _engine()
    return created


def put_object(key: str, data: bytes, content_type: str) -> None:
    with _engine().begin() as connection:
        connection.execute(
            text(
                "INSERT INTO demo_storage_objects (key, content_type, data)"
                " VALUES (:key, :content_type, :data)"
                " ON CONFLICT (key) DO UPDATE SET content_type = EXCLUDED.content_type,"
                " data = EXCLUDED.data, created_at = now()"
            ),
            {"key": key, "content_type": content_type, "data": data},
        )


def get(key: str) -> tuple[bytes, str] | None:
    """The object's bytes and content type, or None if it is missing."""
    with _engine().connect() as connection:
        row = connection.execute(
            text("SELECT data, content_type FROM demo_storage_objects WHERE key = :key"),
            {"key": key},
        ).first()
    return (bytes(row[0]), row[1]) if row else None


def delete_object(key: str) -> None:
    with _engine().begin() as connection:
        connection.execute(text("DELETE FROM demo_storage_objects WHERE key = :key"), {"key": key})


def keys_under(prefix: str) -> Iterator[str]:
    with _engine().connect() as connection:
        rows = connection.execute(
            text("SELECT key FROM demo_storage_objects WHERE starts_with(key, :prefix)"),
            {"prefix": prefix},
        ).all()
    return iter([row[0] for row in rows])


def delete_prefix(prefix: str) -> int:
    with _engine().begin() as connection:
        result = connection.execute(
            text("DELETE FROM demo_storage_objects WHERE starts_with(key, :prefix)"),
            {"prefix": prefix},
        )
    return int(result.rowcount or 0)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALT)


def signed_url(key: str) -> str:
    token = _serializer().dumps(key)
    return url_for("files.read_file", token=token)


def key_from_token(token: str, max_age: int) -> str | None:
    """The key a link was signed for, or None if the link is forged or older than ``max_age``."""
    try:
        key = _serializer().loads(token, max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None
    return key if isinstance(key, str) else None
