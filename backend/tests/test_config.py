import pytest

from app.config import ConfigError, get_config, normalize_database_url

GOOD_PRODUCTION_ENV = {
    "SECRET_KEY": "a-long-random-secret-value",
    "DATA_ENCRYPTION_KEY": "q83vZ1Y2xW9pL0aB5cD7eF1gH3iJ5kL7mN9oP1qR3sT=",
    "HASH_PEPPER": "another-long-random-pepper",
    "DATABASE_URL": "postgres://u:p@db.example.test:5432/tibadirect",
    "S3_ACCESS_KEY_ID": "bucket-user-for-this-test",
    "S3_SECRET_ACCESS_KEY": "bucket-password-for-this-test",
}


def _set_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ],
)
def test_database_url_uses_the_psycopg_driver(url: str, expected: str) -> None:
    assert normalize_database_url(url) == expected


def test_production_accepts_real_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, GOOD_PRODUCTION_ENV)
    config = get_config("production")
    assert config.APP_ENV == "production"
    assert config.SQLALCHEMY_DATABASE_URI.startswith("postgresql+psycopg://")


@pytest.mark.parametrize(
    ("name", "bad_value"),
    [
        ("SECRET_KEY", "change-me"),
        ("DATA_ENCRYPTION_KEY", "ZGV2LW9ubHkta2V5LWRvLW5vdC11c2UtaW4tcHJvZCE="),
        ("HASH_PEPPER", "change-me-too"),
        ("HASH_PEPPER", ""),
        ("S3_ACCESS_KEY_ID", "tibadirect-dev"),
        ("S3_SECRET_ACCESS_KEY", "tibadirect-dev-secret"),
        ("S3_SECRET_ACCESS_KEY", ""),
    ],
)
def test_production_refuses_default_secrets(
    monkeypatch: pytest.MonkeyPatch, name: str, bad_value: str
) -> None:
    _set_env(monkeypatch, {**GOOD_PRODUCTION_ENV, name: bad_value})
    with pytest.raises(ConfigError, match=name):
        get_config("production")


def test_production_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, GOOD_PRODUCTION_ENV)
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        get_config("production")


def test_test_config_refuses_a_non_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://tiba:tiba@localhost/tibadirect")
    with pytest.raises(ConfigError, match="_test"):
        get_config("test")


def test_test_config_forces_mock_paypal_and_no_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAYPAL_MODE", "sandbox")
    monkeypatch.setenv("LLM_API_KEY", "sk-should-not-be-used")
    config = get_config("test")
    assert config.PAYPAL_MODE == "mock"
    assert config.LLM_API_KEY == ""


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ConfigError, match="Unknown APP_ENV"):
        get_config("staging")


def test_invalid_integer_setting_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_PORT", "abc")
    with pytest.raises(ConfigError, match="SMTP_PORT"):
        get_config("development")
