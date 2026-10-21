"""Custom column types."""

from typing import Any

from sqlalchemy import LargeBinary
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.security import crypto


class EncryptedBytes(TypeDecorator[bytes]):
    """Bytes encrypted with AES-256-GCM in the database and plain in Python.

    The column stores ``nonce || ciphertext || tag``. A ``str`` is stored as UTF-8. Use a keyed
    lookup hash column next to it for equality matching; ciphertext is never comparable.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: bytes | str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        plain = value.encode() if isinstance(value, str) else bytes(value)
        return crypto.encrypt(plain)

    def process_result_value(self, value: Any, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        return crypto.decrypt(bytes(value))
