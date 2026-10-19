from unittest.mock import patch

from flask.testing import FlaskClient


def test_health_ok(client: FlaskClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body == {"status": "ok", "version": "0.1.0", "db": "up", "redis": "up"}


def test_health_degraded_when_redis_is_down(client: FlaskClient) -> None:
    with patch("app.services.health_service.check_redis", return_value=False):
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "degraded"
    assert response.get_json()["redis"] == "down"


def test_health_error_when_database_is_down(client: FlaskClient) -> None:
    with patch("app.services.health_service.check_database", return_value=False):
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.get_json()["status"] == "error"
    assert response.get_json()["db"] == "down"


def test_health_is_documented_in_openapi(client: FlaskClient) -> None:
    spec = client.get("/api/openapi.json").get_json()
    assert spec["openapi"] == "3.0.3"
    operation = spec["paths"]["/api/health"]["get"]
    assert "200" in operation["responses"]
    assert "503" in operation["responses"]
