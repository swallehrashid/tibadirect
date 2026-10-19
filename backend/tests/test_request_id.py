import re

from flask.testing import FlaskClient

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def test_request_id_is_generated(client: FlaskClient) -> None:
    response = client.get("/api/health")
    assert UUID_RE.match(response.headers["X-Request-ID"])


def test_valid_request_id_is_echoed(client: FlaskClient) -> None:
    response = client.get("/api/health", headers={"X-Request-ID": "trace-12345678"})
    assert response.headers["X-Request-ID"] == "trace-12345678"


def test_unsafe_request_id_is_replaced(client: FlaskClient) -> None:
    response = client.get("/api/health", headers={"X-Request-ID": "bad id\twith spaces"})
    assert UUID_RE.match(response.headers["X-Request-ID"])


def test_request_id_appears_in_error_body(client: FlaskClient) -> None:
    response = client.get("/api/nope", headers={"X-Request-ID": "trace-abcdefgh"})
    assert response.get_json()["error"]["request_id"] == "trace-abcdefgh"
