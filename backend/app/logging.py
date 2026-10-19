"""Structured JSON logging with a filter that scrubs personal data from every record."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from flask import Flask, g, has_request_context

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Kenyan numbers (+2547.., 07.., 01..) and any other international-looking number.
PHONE_RE = re.compile(
    r"(?<![\w-])(?:\+?254[\s-]?|0)[17]\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\w-])|(?<![\w-])\+\d{9,15}(?![\w-])"
)
# Long digit runs (IDs, account numbers). Digits inside UUIDs and slugs are left alone.
DIGITS_RE = re.compile(r"(?<![\w-])\d{6,}(?![\w-])")

_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
    "request_id",
}

_HANDLER_MARK = "_tibadirect_handler"


def scrub(text: str) -> str:
    """Replace emails, phone numbers and long digit runs with placeholders."""
    text = EMAIL_RE.sub("[email]", text)
    text = PHONE_RE.sub("[phone]", text)
    return DIGITS_RE.sub("[digits]", text)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub_value(v) for v in value]
    return value


class PiiScrubFilter(logging.Filter):
    """Scrubs the message, arguments, exception text and extra fields of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(record.getMessage())
        record.args = ()
        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = scrub(formatter.formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = scrub(record.exc_text)
        for key in list(record.__dict__):
            if key not in _STANDARD_ATTRS:
                record.__dict__[key] = _scrub_value(record.__dict__[key])
        return True


class RequestIdFilter(logging.Filter):
    """Attaches the current request ID (if any) to the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = g.get("request_id") if has_request_context() else None
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


def make_handler(stream: TextIO) -> logging.Handler:
    """A handler that writes scrubbed JSON lines to ``stream``."""
    handler = logging.StreamHandler(stream)
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    handler.addFilter(PiiScrubFilter())
    return handler


def configure_logging(app: Flask) -> None:
    """Install the JSON handler on the root logger (idempotent across repeated app creation)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)

    root.addHandler(make_handler(sys.stdout))
    root.setLevel(app.config.get("LOG_LEVEL", "INFO"))

    # Werkzeug's access log prints raw paths and client addresses;
    # the app writes its own request line instead.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
