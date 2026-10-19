"""Configuration classes. Values come from environment variables (see .env.example)."""

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

# Development-only key from .env.example (base64 of 32 bytes). Never valid in production.
DEV_DATA_ENCRYPTION_KEY = "ZGV2LW9ubHkta2V5LWRvLW5vdC11c2UtaW4tcHJvZCE="

# Values that must never reach production. They are the placeholders from .env.example.
INSECURE_DEFAULTS: dict[str, set[str]] = {
    "SECRET_KEY": {"", "change-me"},
    "DATA_ENCRYPTION_KEY": {"", DEV_DATA_ENCRYPTION_KEY},
    "HASH_PEPPER": {"", "change-me", "change-me-too"},
}

DEFAULT_SYNTHETIC_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"
DEFAULT_DATABASE_URL = "postgresql+psycopg://tiba:tiba@localhost:5432/tibadirect"
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://tiba:tiba@localhost:5432/tibadirect_test"


class ConfigError(RuntimeError):
    """Raised when the configuration is unsafe or invalid for the selected environment."""


def normalize_database_url(url: str) -> str:
    """Point any Postgres URL at the psycopg 3 driver (Render supplies ``postgres://``)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_json(name: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


class BaseConfig:
    """Settings shared by every environment. Read at instantiation, after .env is loaded."""

    APP_ENV = "development"
    DEBUG = False
    TESTING = False

    # flask-smorest: OpenAPI 3.0.3 document at /api/openapi.json
    API_TITLE = "TibaDirect API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.3"
    OPENAPI_URL_PREFIX = "/api"
    OPENAPI_JSON_PATH = "openapi.json"

    def __init__(self) -> None:
        self.SECRET_KEY = _env("SECRET_KEY", "change-me")
        self.APP_BASE_URL = _env("APP_BASE_URL", "http://localhost:5173")
        self.DEMO_BANNER = _env_bool("DEMO_BANNER", True)
        self.LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
        self.APP_VERSION = _env("APP_VERSION", "0.1.0")

        self.SQLALCHEMY_DATABASE_URI = normalize_database_url(
            _env("DATABASE_URL", DEFAULT_DATABASE_URL)
        )
        self.SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
        self.REDIS_URL = _env("REDIS_URL", "redis://localhost:6379/0")

        self.DATA_ENCRYPTION_KEY = _env("DATA_ENCRYPTION_KEY")
        self.HASH_PEPPER = _env("HASH_PEPPER", "change-me-too")

        self.S3_ENDPOINT_URL = _env("S3_ENDPOINT_URL", "http://localhost:9000")
        self.S3_BUCKET = _env("S3_BUCKET", "tibadirect-uploads")
        self.S3_ACCESS_KEY_ID = _env("S3_ACCESS_KEY_ID")
        self.S3_SECRET_ACCESS_KEY = _env("S3_SECRET_ACCESS_KEY")
        self.S3_REGION = _env("S3_REGION", "us-east-1")

        self.SMTP_HOST = _env("SMTP_HOST", "localhost")
        self.SMTP_PORT = _env_int("SMTP_PORT", 1025)
        self.SMTP_USERNAME = _env("SMTP_USERNAME")
        self.SMTP_PASSWORD = _env("SMTP_PASSWORD")
        self.MAIL_FROM = _env("MAIL_FROM", "no-reply@tibadirect.test")

        self.PAYPAL_MODE = _env("PAYPAL_MODE", "mock")
        self.PAYPAL_API_BASE = _env("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")
        self.PAYPAL_CLIENT_ID = _env("PAYPAL_CLIENT_ID")
        self.PAYPAL_CLIENT_SECRET = _env("PAYPAL_CLIENT_SECRET")
        self.PAYPAL_WEBHOOK_ID = _env("PAYPAL_WEBHOOK_ID")
        self.HOSPITAL_PAYPAL_CREDENTIALS = _env_json("HOSPITAL_PAYPAL_CREDENTIALS", {})

        self.LLM_PROVIDER = _env("LLM_PROVIDER", "anthropic")
        self.LLM_API_KEY = _env("LLM_API_KEY")
        self.LLM_MODEL = _env("LLM_MODEL")
        self.LLM_TIMEOUT_SECONDS = _env_int("LLM_TIMEOUT_SECONDS", 30)

        self.FX_PROVIDER = _env("FX_PROVIDER", "static")
        self.FX_STATIC_USD_KES = Decimal(_env("FX_STATIC_USD_KES", "129.00"))
        self.BUFFER_BPS_DEFAULT = _env_int("BUFFER_BPS_DEFAULT", 500)
        self.TRANCHE_MIN_KES = _env_int("TRANCHE_MIN_KES", 20000)
        self.TRANCHE_FRACTION_BPS = _env_int("TRANCHE_FRACTION_BPS", 2500)
        self.ESTIMATE_SETTLEMENT_CAP_BPS = _env_int("ESTIMATE_SETTLEMENT_CAP_BPS", 8000)
        self.REFUND_MIN_USD_MINOR = _env_int("REFUND_MIN_USD_MINOR", 100)
        self.FOLLOW_UP_HOLD_DAYS = _env_int("FOLLOW_UP_HOLD_DAYS", 14)
        self.TWO_PERSON_THRESHOLD_KES = _env_int("TWO_PERSON_THRESHOLD_KES", 200000)

        self.SYNTHETIC_DATA_DIR = _env("SYNTHETIC_DATA_DIR") or str(DEFAULT_SYNTHETIC_DATA_DIR)
        self.SEED_HOSPITAL_PAYOUT_EMAILS = _env_json("SEED_HOSPITAL_PAYOUT_EMAILS", {})

        self.RAW_DOC_RETENTION_HOURS = _env_int("RAW_DOC_RETENTION_HOURS", 24)
        self.AGENT_ENABLED = _env_bool("AGENT_ENABLED", False)
        self.APPEAL_CHECKER_USE_LLM = _env_bool("APPEAL_CHECKER_USE_LLM", False)


class DevelopmentConfig(BaseConfig):
    APP_ENV = "development"
    DEBUG = True


class TestConfig(BaseConfig):
    """Hermetic settings for pytest: mock PayPal, no language model key, a separate database."""

    APP_ENV = "test"
    TESTING = True

    def __init__(self) -> None:
        super().__init__()
        test_url = normalize_database_url(_env("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
        database = test_url.rsplit("/", 1)[-1].split("?")[0]
        if not database.endswith("_test"):
            raise ConfigError("TEST_DATABASE_URL must point at a database whose name ends in _test")
        self.SQLALCHEMY_DATABASE_URI = test_url
        self.REDIS_URL = _env("TEST_REDIS_URL", self.REDIS_URL)
        self.PAYPAL_MODE = "mock"
        self.LLM_API_KEY = ""
        self.DATA_ENCRYPTION_KEY = self.DATA_ENCRYPTION_KEY or DEV_DATA_ENCRYPTION_KEY
        self.LOG_LEVEL = _env("LOG_LEVEL", "WARNING").upper()
        self.SECRET_KEY = "test-secret-key"


class ProductionConfig(BaseConfig):
    """Refuses to start with placeholder secrets."""

    APP_ENV = "production"

    def __init__(self) -> None:
        super().__init__()
        unsafe = [name for name, bad in INSECURE_DEFAULTS.items() if getattr(self, name, "") in bad]
        if unsafe:
            raise ConfigError(
                "Refusing to start in production with default or empty values for: "
                + ", ".join(sorted(unsafe))
            )
        if not os.environ.get("DATABASE_URL"):
            raise ConfigError("DATABASE_URL is required in production")


_CONFIGS: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "test": TestConfig,
    "production": ProductionConfig,
}


def get_config(config_name: str | None = None) -> BaseConfig:
    """Build the config for ``config_name`` (default: the ``APP_ENV`` variable)."""
    name = (config_name or _env("APP_ENV", "development")).lower()
    try:
        return _CONFIGS[name]()
    except KeyError as exc:
        raise ConfigError(f"Unknown APP_ENV {name!r}; use development, test or production") from exc
