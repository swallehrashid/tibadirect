"""Encryption at rest, keyed lookup hashes and random identifiers.

- ``encrypt`` / ``decrypt``: AES-256-GCM with a random 12-byte nonce prepended to the ciphertext.
  The key is ``DATA_ENCRYPTION_KEY`` (base64 of 32 bytes).
- ``lookup_hash``: HMAC-SHA256 keyed with ``HASH_PEPPER`` over a normalised value, so rows can be
  matched without decrypting them.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
import unicodedata
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app

NONCE_BYTES = 12
KEY_BYTES = 32
_SLUG_LENGTH = 12


class CryptoError(Exception):
    """Encryption is misconfigured, or a ciphertext is malformed or has been tampered with."""


@lru_cache(maxsize=8)
def _aesgcm_for(key_b64: str) -> AESGCM:
    try:
        key = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CryptoError("DATA_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise CryptoError("DATA_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return AESGCM(key)


def _cipher(key_b64: str | None) -> AESGCM:
    key_b64 = key_b64 if key_b64 is not None else current_app.config.get("DATA_ENCRYPTION_KEY", "")
    if not key_b64:
        raise CryptoError("DATA_ENCRYPTION_KEY is not set")
    return _aesgcm_for(key_b64)


def encrypt(plain: bytes, *, key: str | None = None) -> bytes:
    """Encrypt ``plain``. Returns ``nonce || ciphertext || tag``."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + _cipher(key).encrypt(nonce, plain, None)


def decrypt(blob: bytes, *, key: str | None = None) -> bytes:
    """Decrypt a value produced by ``encrypt``. Raises ``CryptoError`` if it was altered."""
    if len(blob) < NONCE_BYTES + 16:
        raise CryptoError("Ciphertext is too short")
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return _cipher(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise CryptoError("Ciphertext failed authentication") from exc


def normalize(value: str) -> str:
    """Canonical form used before hashing: NFKC, trimmed, case-folded, single spaces."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def lookup_hash(value: str, *, context: str = "", pepper: str | None = None) -> str:
    """HMAC-SHA256 (hex) of the normalised value, keyed with the pepper.

    ``context`` separates uses of the hash (for example ``"login"`` or ``"otp"``) so equal values
    in different roles never produce equal digests.
    """
    pepper = pepper if pepper is not None else current_app.config.get("HASH_PEPPER", "")
    if not pepper:
        raise CryptoError("HASH_PEPPER is not set")
    message = normalize(value)
    if context:
        message = f"{context}\x00{message}"
    return hmac.new(pepper.encode(), message.encode(), hashlib.sha256).hexdigest()


def hash_ip(ip: str | None) -> str | None:
    """Keyed hash of a client address: enough to spot repeats, useless for locating anyone."""
    return lookup_hash(ip, context="ip") if ip else None


def hashes_match(left: str, right: str) -> bool:
    """Constant-time comparison of two hex digests."""
    return hmac.compare_digest(left.encode(), right.encode())


def new_public_slug() -> str:
    """Random 12-character base32 slug for public case URLs (60 bits of entropy)."""
    return base64.b32encode(secrets.token_bytes(8)).decode().rstrip("=").lower()[:_SLUG_LENGTH]


def new_token(nbytes: int = 24) -> str:
    """URL-safe random token. The default gives 32 characters (receipt links)."""
    return secrets.token_urlsafe(nbytes)
