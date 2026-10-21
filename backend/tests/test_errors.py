from flask.testing import FlaskClient


def _error(response) -> dict:
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "request_id"}
    return body["error"]


def test_unknown_route_uses_error_envelope(client: FlaskClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_method_not_allowed(client: FlaskClient) -> None:
    response = client.post("/api/health")
    assert response.status_code == 405
    assert _error(response)["code"] == "method_not_allowed"


def test_validation_error_is_422_with_field_details(client: FlaskClient) -> None:
    response = client.post("/api/_test/echo", json={"name": "x"})
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "validation_error"
    assert "name" in error["details"]["json"]
    assert "amount" in error["details"]["json"]


def test_valid_payload_passes(client: FlaskClient) -> None:
    response = client.post("/api/_test/echo", json={"name": "ok", "amount": 5})
    assert response.status_code == 200
    assert response.get_json() == {"name": "ok", "amount": 5}


def test_app_error_keeps_its_stable_code(client: FlaskClient) -> None:
    response = client.get("/api/_test/not-found")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "case_not_found"
    assert error["message"] == "Case not found."


def test_conflict_carries_details(client: FlaskClient) -> None:
    response = client.get("/api/_test/conflict")
    assert response.status_code == 409
    error = _error(response)
    assert error["code"] == "invalid_transition"
    assert error["details"] == {"from": "draft"}


def test_forbidden_default_message(client: FlaskClient) -> None:
    response = client.get("/api/_test/forbidden")
    assert response.status_code == 403
    assert _error(response)["code"] == "forbidden"


def test_unexpected_exception_does_not_leak_details(client: FlaskClient) -> None:
    response = client.get("/api/_test/boom")
    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == "internal_error"
    assert "secret detail" not in response.get_data(as_text=True)
    assert "jane.doe" not in response.get_data(as_text=True)
