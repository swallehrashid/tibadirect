"""Request ID middleware and the one-line request log."""

import logging
import re
import time
import uuid

from flask import Flask, Response, g, request

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

request_logger = logging.getLogger("app.request")


def _incoming_request_id() -> str:
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    return supplied if _VALID_REQUEST_ID.match(supplied) else str(uuid.uuid4())


def init_request_id(app: Flask) -> None:
    """Accept or generate ``X-Request-ID``, echo it, and log one line per request.

    The log line records the route template, never the raw path or any body, so identifiers
    and personal data in URLs and payloads stay out of the logs.
    """

    @app.before_request
    def _start() -> None:
        g.request_id = _incoming_request_id()
        g.request_started = time.perf_counter()

    @app.after_request
    def _finish(response: Response) -> Response:
        request_id = g.get("request_id")
        if request_id:
            response.headers[REQUEST_ID_HEADER] = request_id
        started = g.get("request_started")
        duration_ms = round((time.perf_counter() - started) * 1000, 1) if started else None
        rule = request.url_rule.rule if request.url_rule else "unmatched"
        request_logger.info(
            "request",
            extra={
                "method": request.method,
                "route": rule,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
