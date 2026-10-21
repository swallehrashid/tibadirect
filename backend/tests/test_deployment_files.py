"""The deployment files say what the documentation says they do."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())


def test_the_blueprint_has_a_web_service_a_worker_a_queue_store_and_a_database() -> None:
    blueprint = _load("render.yaml")
    services = {s["name"]: s for s in blueprint["services"]}
    web, worker, redis = (
        services["tibadirect-web"],
        services["tibadirect-worker"],
        services["tibadirect-redis"],
    )
    assert (web["type"], web["runtime"], web["healthCheckPath"]) == ("web", "docker", "/api/health")
    assert (worker["type"], worker["runtime"]) == ("worker", "docker")
    assert "app.jobs.scheduler" in worker["dockerCommand"]
    assert "app.jobs.worker" in worker["dockerCommand"]
    assert redis["type"] == "keyvalue"
    assert redis["ipAllowList"] == []
    assert redis["maxmemoryPolicy"] == "noeviction"  # evicting a queued job would lose work
    assert blueprint["databases"][0]["name"] == "tibadirect-db"


def test_web_and_worker_share_one_set_of_secrets() -> None:
    blueprint = _load("render.yaml")
    group = next(g for g in blueprint["envVarGroups"] if g["name"] == "tibadirect-secrets")
    assert {v["key"] for v in group["envVars"]} == {
        "SECRET_KEY",
        "HASH_PEPPER",
        "DATA_ENCRYPTION_KEY",
    }
    for service in blueprint["services"][:2]:
        keys = {v.get("key") for v in service["envVars"]}
        assert {"fromGroup"} <= {k for v in service["envVars"] for k in v}
        # the same three names must not also be set per service, or each would get its own value
        assert not keys & {"SECRET_KEY", "HASH_PEPPER", "DATA_ENCRYPTION_KEY"}


def test_the_blueprint_wires_the_database_and_the_queue_store_to_both_services() -> None:
    for service in _load("render.yaml")["services"][:2]:
        env = {v["key"]: v for v in service["envVars"] if "key" in v}
        assert env["DATABASE_URL"]["fromDatabase"] == {
            "name": "tibadirect-db",
            "property": "connectionString",
        }
        assert env["REDIS_URL"]["fromService"]["name"] == "tibadirect-redis"
        assert env["APP_ENV"]["value"] == "production"
        assert env["PROXY_FIX_COUNT"]["value"] == "1"


def test_no_secret_value_is_written_into_the_blueprint() -> None:
    text = (ROOT / "render.yaml").read_text()
    for name in ("S3_SECRET_ACCESS_KEY", "SMTP_PASSWORD", "PAYPAL_CLIENT_SECRET", "LLM_API_KEY"):
        assert re.search(rf"key: {name}\n\s+sync: false", text), name


def test_every_setting_the_blueprint_names_is_one_the_app_reads() -> None:
    source = (ROOT / "backend" / "app" / "config.py").read_text()
    read_by_config = set(re.findall(r'_env\w*\(\s*"([A-Z0-9_]+)"', source))
    read_by_config |= set(re.findall(r'os\.environ\.get\(\s*"([A-Z0-9_]+)"', source))
    for service in _load("render.yaml")["services"][:2]:
        for entry in service["envVars"]:
            if "key" in entry:
                assert entry["key"] in read_by_config, entry["key"]


def test_the_dockerfile_builds_a_non_root_image_that_migrates_then_serves() -> None:
    text = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.12-slim" in text
    assert "tesseract-ocr" in text
    assert "poppler-utils" in text
    assert re.search(r"^USER tiba$", text, re.MULTILINE)
    assert "db upgrade" in text
    assert "gunicorn wsgi:app --workers 2 --threads 4" in text
    assert "--bind 0.0.0.0:${PORT}" in text
    assert "COPY --from=frontend /dist/ frontend/dist/" in text


def test_the_build_context_excludes_secrets_and_tests_but_keeps_the_consent_wording() -> None:
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    for excluded in (".env", ".git", "backend/tests", "/docs", "backend/.venv"):
        assert excluded in ignore
    assert "!.env.example" in ignore
    assert not any(line.strip() == "*.md" for line in ignore)  # that would drop consent_texts
    assert (ROOT / "backend" / "app" / "consent_texts" / "public_page.en.md").exists()


def test_compose_adds_the_application_services_under_a_profile() -> None:
    compose = _load("docker-compose.yml")["services"]
    for name in ("api", "worker", "scheduler"):
        assert compose[name]["profiles"] == ["app"]
    for name in ("postgres", "redis", "minio", "mailpit"):
        assert "profiles" not in compose[name]  # `make up` must still start only the infrastructure
    assert compose["worker"]["command"] == ["python", "-m", "app.jobs.worker"]
    assert compose["scheduler"]["command"] == ["python", "-m", "app.jobs.scheduler"]
    assert compose["api"]["environment"]["DATABASE_URL"].endswith("@postgres:5432/tibadirect")
    assert compose["worker"]["depends_on"]["api"] == {"condition": "service_healthy"}


def test_the_example_settings_cover_the_new_ports() -> None:
    example = (ROOT / ".env.example").read_text()
    for name in ("API_HOST_PORT", "PROXY_FIX_COUNT", "JOBS_ASYNC", "MAIL_BACKEND"):
        assert re.search(rf"^{name}=", example, re.MULTILINE), name
