import io
import json
import logging

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.logging import make_handler, scrub


@pytest.fixture
def log_lines(app: Flask):
    stream = io.StringIO()
    handler = make_handler(stream)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    root.removeHandler(handler)
    root.setLevel(previous_level)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mail jane.doe@example.test now", "mail [email] now"),
        ("call 0712345678 today", "call [phone] today"),
        ("call +254712345678 today", "call [phone] today"),
        ("call 0712 345 678", "call [phone]"),
        ("id 12345678 done", "id [digits] done"),
        ("order 1234 is fine", "order 1234 is fine"),
        ("req 6f1c2d3e-1234-5678-9abc-def012345678", "req 6f1c2d3e-1234-5678-9abc-def012345678"),
    ],
)
def test_scrub(raw: str, expected: str) -> None:
    assert scrub(raw) == expected


def test_records_are_json_and_scrubbed(log_lines) -> None:
    logging.getLogger("tests.sample").warning(
        "donor %s phone %s", "jane.doe@example.test", "0712345678", extra={"note": "id 87654321"}
    )
    record = log_lines()[-1]
    assert record["level"] == "WARNING"
    assert record["message"] == "donor [email] phone [phone]"
    assert record["note"] == "id [digits]"
    assert "jane.doe" not in json.dumps(record)


def test_exception_text_is_scrubbed(log_lines) -> None:
    try:
        raise ValueError("bad value for jane.doe@example.test")
    except ValueError:
        logging.getLogger("tests.sample").exception("failed")
    record = log_lines()[-1]
    assert "jane.doe" not in record["exception"]
    assert "[email]" in record["exception"]


def test_request_line_has_route_and_request_id_only(client: FlaskClient, log_lines) -> None:
    client.post(
        "/api/_test/echo?token=abcdef123456",
        json={"name": "Jane Doe", "amount": 12345678},
        headers={"X-Request-ID": "trace-12345678"},
    )
    line = next(r for r in log_lines() if r["logger"] == "app.request")
    assert line["route"] == "/api/_test/echo"
    assert line["method"] == "POST"
    assert line["status"] == 200
    assert line["request_id"] == "trace-12345678"
    serialised = json.dumps(line)
    assert "Jane" not in serialised
    assert "12345678" not in serialised.replace("trace-12345678", "")
    assert "token" not in serialised


def test_unhandled_error_is_logged_with_request_id(client: FlaskClient, log_lines) -> None:
    client.get("/api/_test/boom", headers={"X-Request-ID": "trace-87654321"})
    error_line = next(r for r in log_lines() if r["message"] == "Unhandled exception")
    assert error_line["request_id"] == "trace-87654321"
    assert "jane.doe" not in json.dumps(error_line)
