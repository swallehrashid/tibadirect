"""Errors raised by the PayPal client, mapped to this API's codes and HTTP statuses."""

from typing import Any

from app.errors import AppError

# PayPal's ``details[].issue`` values we handle specially: (HTTP status, our error code, message).
# Messages are ours and never repeat PayPal's text, which can name the payer.
ISSUE_MAP: dict[str, tuple[int, str, str]] = {
    "INSTRUMENT_DECLINED": (
        422,
        "payment_declined",
        "PayPal declined that payment method. Choose another and try again.",
    ),
    "PAYER_ACTION_REQUIRED": (
        422,
        "payer_action_required",
        "PayPal needs the payer to take another step before the payment can finish.",
    ),
    "ORDER_NOT_APPROVED": (
        422,
        "order_not_approved",
        "The payment has not been approved in PayPal yet.",
    ),
    "ORDER_ALREADY_CAPTURED": (
        409,
        "order_already_captured",
        "This payment was already completed.",
    ),
    "CAPTURE_FULLY_REFUNDED": (
        409,
        "capture_fully_refunded",
        "This payment has already been refunded in full.",
    ),
    "INSUFFICIENT_FUNDS": (
        502,
        "paypal_insufficient_funds",
        "The PayPal account does not hold enough money for that.",
    ),
    "RESOURCE_NOT_FOUND": (404, "paypal_not_found", "PayPal does not know that payment."),
    "INVALID_RESOURCE_ID": (404, "paypal_not_found", "PayPal does not know that payment."),
}

UNAVAILABLE = (502, "paypal_unavailable", "PayPal could not be reached. Please try again shortly.")
REJECTED = (502, "paypal_rejected", "PayPal rejected the request.")
AUTH_FAILED = (502, "paypal_auth_failed", "The PayPal credentials were refused.")


class PayPalError(AppError):
    """A PayPal call failed. ``issue`` and ``debug_id`` are for the logs and admins.

    ``retryable`` is true for outages and rate limits, where trying again with the same
    idempotency key is safe.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        http_status: int | None = None,
        name: str | None = None,
        issue: str | None = None,
        debug_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        details: dict[str, Any] = {}
        if issue:
            details["issue"] = issue
        if debug_id:
            details["paypal_debug_id"] = debug_id
        super().__init__(message, code=code, status_code=status_code, details=details)
        self.http_status = http_status
        self.name = name
        self.issue = issue
        self.debug_id = debug_id
        self.retryable = retryable


def from_response(
    http_status: int, body: dict[str, Any] | None, debug_id: str | None
) -> PayPalError:
    """Build the error for a non-2xx PayPal response."""
    body = body or {}
    name = body.get("name") if isinstance(body.get("name"), str) else None
    details = body.get("details")
    issues = [
        d["issue"]
        for d in (details if isinstance(details, list) else [])
        if isinstance(d, dict) and isinstance(d.get("issue"), str)
    ]
    # PayPal reports some failures through ``name`` alone (payouts: INSUFFICIENT_FUNDS).
    candidates = [*issues, name] if name else issues
    for candidate in candidates:
        if candidate in ISSUE_MAP:
            status, code, message = ISSUE_MAP[candidate]
            return PayPalError(
                status_code=status,
                code=code,
                message=message,
                http_status=http_status,
                name=name,
                issue=candidate,
                debug_id=debug_id,
            )
    issue = issues[0] if issues else None
    if http_status == 401:
        status, code, message = AUTH_FAILED
        retryable = False
    elif http_status == 429 or http_status >= 500:
        status, code, message = UNAVAILABLE
        retryable = True
    else:
        status, code, message = REJECTED
        retryable = False
    return PayPalError(
        status_code=status,
        code=code,
        message=message,
        http_status=http_status,
        name=name,
        issue=issue,
        debug_id=debug_id,
        retryable=retryable,
    )


def unavailable(reason: str) -> PayPalError:
    """The network failed before PayPal answered (timeout, connection reset)."""
    status, code, message = UNAVAILABLE
    return PayPalError(status_code=status, code=code, message=message, issue=reason, retryable=True)


def malformed(what: str) -> PayPalError:
    """PayPal answered, but not in the shape its documentation promises."""
    return PayPalError(
        status_code=502,
        code="paypal_bad_response",
        message="PayPal sent an answer we could not read.",
        issue=what,
    )
