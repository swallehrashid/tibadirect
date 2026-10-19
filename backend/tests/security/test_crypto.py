import base64
import re

import pytest
from flask import Flask

from app.security import crypto
from app.security.crypto import CryptoError

KEY = base64.b64encode(bytes(range(32))).decode()
OTHER_KEY = base64.b64encode(bytes(range(1, 33))).decode()


def test_round_trip() -> None:
    blob = crypto.encrypt(b"IP/2026/00123", key=KEY)
    assert crypto.decrypt(blob, key=KEY) == b"IP/2026/00123"


def test_ciphertext_hides_the_plaintext_and_is_randomised() -> None:
    first = crypto.encrypt(b"same value", key=KEY)
    second = crypto.encrypt(b"same value", key=KEY)
    assert b"same value" not in first
    assert first != second
    assert len(first) == crypto.NONCE_BYTES + len(b"same value") + 16


def test_empty_value_round_trips() -> None:
    assert crypto.decrypt(crypto.encrypt(b"", key=KEY), key=KEY) == b""


@pytest.mark.parametrize("position", [0, 5, 12, 14, -1])
def test_flipping_any_byte_is_detected(position: int) -> None:
    blob = bytearray(crypto.encrypt(b"patient reference", key=KEY))
    blob[position] ^= 0x01
    with pytest.raises(CryptoError):
        crypto.decrypt(bytes(blob), key=KEY)


def test_truncated_ciphertext_is_rejected() -> None:
    blob = crypto.encrypt(b"patient reference", key=KEY)
    with pytest.raises(CryptoError):
        crypto.decrypt(blob[:-1], key=KEY)
    with pytest.raises(CryptoError, match="too short"):
        crypto.decrypt(b"short", key=KEY)


def test_wrong_key_is_rejected() -> None:
    blob = crypto.encrypt(b"patient reference", key=KEY)
    with pytest.raises(CryptoError):
        crypto.decrypt(blob, key=OTHER_KEY)


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("", "not set"),
        ("not base64 !!", "base64"),
        (base64.b64encode(b"too short").decode(), "32 bytes"),
    ],
)
def test_bad_keys_fail_loudly(key: str, message: str) -> None:
    with pytest.raises(CryptoError, match=message):
        crypto.encrypt(b"x", key=key)


def test_key_comes_from_app_config(app: Flask) -> None:
    with app.app_context():
        assert crypto.decrypt(crypto.encrypt(b"hello")) == b"hello"


def test_lookup_hash_is_stable_hex_sha256() -> None:
    digest = crypto.lookup_hash("IP/2026/00123", pepper="pepper")
    assert digest == crypto.lookup_hash("IP/2026/00123", pepper="pepper")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_lookup_hash_normalises_the_value() -> None:
    reference = crypto.lookup_hash("IP/2026/00123", pepper="p")
    assert crypto.lookup_hash("  ip/2026/00123 ", pepper="p") == reference
    assert crypto.lookup_hash("Family1@Example.test", pepper="p") == crypto.lookup_hash(
        "family1@example.test", pepper="p"
    )
    assert crypto.lookup_hash("ip/2026/ 00123", pepper="p") != crypto.lookup_hash(
        "ip/2026/00124", pepper="p"
    )


def test_lookup_hash_depends_on_pepper_and_context() -> None:
    base = crypto.lookup_hash("value", pepper="one")
    assert crypto.lookup_hash("value", pepper="two") != base
    assert crypto.lookup_hash("value", pepper="one", context="otp") != base
    assert crypto.lookup_hash("value", pepper="one", context="otp") != crypto.lookup_hash(
        "value", pepper="one", context="login"
    )


def test_lookup_hash_requires_a_pepper() -> None:
    with pytest.raises(CryptoError, match="HASH_PEPPER"):
        crypto.lookup_hash("value", pepper="")


def test_hashes_match_is_exact() -> None:
    digest = crypto.lookup_hash("value", pepper="p")
    assert crypto.hashes_match(digest, digest)
    assert not crypto.hashes_match(digest, crypto.lookup_hash("other", pepper="p"))


def test_public_slug_is_12_lowercase_base32_characters() -> None:
    slugs = {crypto.new_public_slug() for _ in range(200)}
    assert len(slugs) == 200
    assert all(re.fullmatch(r"[a-z2-7]{12}", slug) for slug in slugs)


def test_token_is_32_url_safe_characters_by_default() -> None:
    tokens = {crypto.new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{32}", token) for token in tokens)
