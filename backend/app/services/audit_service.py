"""The audit trail: who did what to which record.

``record`` adds one row to the caller's transaction, so an action and its audit entry commit or
roll back together. Entries carry the request ID of the request that caused them, an HMAC of the
caller's address and a ``details`` object that never holds personal data: forbidden keys raise
(a bug to fix), and any email, phone number or long digit run inside a string is replaced.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from flask import g, has_request_context, request
from sqlalchemy.orm import Session

from app.extensions import db
from app.logging import scrub
from app.models.enums import ActorType
from app.models.identity import User
from app.models.platform import AuditLog
from app.security import crypto

# ``domain.event`` in lower case, for example ``case.submitted`` or ``confirmation.otp_sent``.
ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
MAX_ACTION_LENGTH = 60
MAX_DETAILS_DEPTH = 4

# Keys that would put personal or clinical data into the trail. Use IDs, counts and codes instead.
FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "email",
        "phone",
        "name",
        "display_name",
        "patient_reference",
        "patient_ref",
        "diagnosis",
        "text",
        "redacted_text",
        "note",
        "message",
        "story",
        "code",
        "otp",
        "password",
        "token",
        "secret",
    }
)


class AuditError(ValueError):
    """The entry cannot be recorded as given (a programming error, never user input)."""


@dataclass(frozen=True)
class Target:
    """The record an action was done to: a type name and an ID (``None`` when there is none)."""

    type: str
    id: uuid.UUID | None


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def target_of(target: Any) -> Target:
    """A ``Target`` for a model instance (``Case`` becomes ``case``) or the argument itself."""
    if isinstance(target, Target):
        return target
    return Target(_snake(type(target).__name__), getattr(target, "id", None))


def _clean_details(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DETAILS_DEPTH:
        raise AuditError("Audit details are nested too deeply")
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AuditError("Audit detail keys must be strings")
            if key.lower() in FORBIDDEN_DETAIL_KEYS:
                raise AuditError(f"Audit details must not contain {key!r}; record an ID instead")
            cleaned[key] = _clean_details(item, depth + 1)
        return cleaned
    if isinstance(value, list | tuple):
        return [_clean_details(item, depth + 1) for item in value]
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "value") and isinstance(value.value, str):  # an enum member
        return value.value
    if value is None or isinstance(value, bool | int | float):
        return value
    raise AuditError(f"Audit details cannot hold a {type(value).__name__}")


def current_request_id() -> str | None:
    """The ID of the request being served, if any."""
    return g.get("request_id") if has_request_context() else None


def record(
    action: str,
    target: Any,
    *,
    actor: User | ActorType,
    approver: User | None = None,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
    session: Session | None = None,
) -> AuditLog:
    """Add an audit row to the current transaction and return it. The caller commits.

    ``actor`` is the signed-in ``User`` or ``ActorType.SYSTEM`` / ``AGENT`` / ``PAYPAL``. For an
    agent action a human approved, pass that person as ``approver``. Outside a request (a job),
    pass the ``request_id`` of the request that enqueued the work to keep the trail connected.
    """
    if len(action) > MAX_ACTION_LENGTH or not ACTION_PATTERN.match(action):
        raise AuditError(f"Audit action {action!r} must look like 'case.submitted'")
    if isinstance(actor, User):
        if approver is not None:
            raise AuditError("Only a non-user actor has an approver")
        actor_type, actor_user = ActorType.USER, actor
    else:
        actor_type, actor_user = actor, approver
    if actor_type == ActorType.USER and actor_user is None:
        raise AuditError("A user actor needs the user")

    found = target_of(target)
    ip_hmac = (
        crypto.hash_ip(request.remote_addr)
        if has_request_context() and request.remote_addr
        else None
    )
    entry = AuditLog(
        actor_type=actor_type,
        actor_user_id=actor_user.id if actor_user else None,
        action=action,
        target_type=found.type,
        target_id=found.id,
        details=_clean_details(details or {}),
        request_id=request_id or current_request_id(),
        ip_hmac=ip_hmac,
        # Stamped here, not by the database, which would give every row of one transaction the
        # same time and leave the order of a case's history undefined.
        created_at=datetime.now(UTC),
    )
    (session or db.session).add(entry)
    (session or db.session).flush()
    return entry
