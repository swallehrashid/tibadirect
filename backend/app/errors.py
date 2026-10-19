"""Application errors and the JSON error envelope.

Every error response has the shape::

    {"error": {"code": "case_not_found", "message": "...", "details": {}, "request_id": "..."}}

Codes are stable strings. Status mapping: validation 422, auth 401, permission 403,
not found 404, conflict or invalid transition 409, rate limit 429.
"""

import logging
from typing import Any

from flask import Flask, g
from flask_smorest import Api
from werkzeug.exceptions import HTTPException

from app.schemas.errors import ErrorEnvelopeSchema

logger = logging.getLogger(__name__)

_STATUS_CODES: dict[int, tuple[str, str]] = {
    400: ("bad_request", "The request could not be understood."),
    401: ("unauthenticated", "Sign in to continue."),
    403: ("forbidden", "You do not have permission to do that."),
    404: ("not_found", "Not found."),
    405: ("method_not_allowed", "This method is not allowed here."),
    409: ("conflict", "This conflicts with the current state."),
    413: ("payload_too_large", "The upload is too large."),
    415: ("unsupported_media_type", "That content type is not supported."),
    422: ("validation_error", "Some fields are not valid."),
    429: ("rate_limited", "Too many requests. Please try again later."),
}


class AppError(Exception):
    """Base class for errors that map to an HTTP response."""

    status_code = 500
    code = "internal_error"
    message = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_error"
    message = "Some fields are not valid."


class AuthenticationRequired(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Sign in to continue."


class PermissionDenied(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to do that."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "Not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "This conflicts with the current state."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please try again later."


def error_payload(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Build the error envelope for the current request."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": g.get("request_id"),
        }
    }


class TibaApi(Api):
    """flask-smorest ``Api`` whose error responses use the TibaDirect envelope."""

    ERROR_SCHEMA = ErrorEnvelopeSchema

    def handle_http_exception(self, error: HTTPException) -> tuple[dict[str, Any], int, dict]:
        status = error.code or 500
        code, message = _STATUS_CODES.get(status, ("internal_error", "Something went wrong."))
        if status >= 500:
            code, message = "internal_error", "Something went wrong."
        headers: dict[str, str] = {}
        details: dict[str, Any] = {}

        data = getattr(error, "data", None)
        if data:
            message = data.get("message", message)
            details = data.get("errors") or data.get("messages") or {}
            headers = data.get("headers", {})

        return error_payload(code, message, details), status, headers


def register_error_handlers(app: Flask) -> None:
    """Handle application errors and anything unexpected with the same envelope."""

    @app.errorhandler(AppError)
    def _handle_app_error(error: AppError) -> tuple[dict[str, Any], int]:
        return error_payload(error.code, error.message, error.details), error.status_code

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception) -> tuple[dict[str, Any], int]:
        logger.error("Unhandled exception", exc_info=error)
        return error_payload("internal_error", "Something went wrong."), 500
