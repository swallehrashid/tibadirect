"""Email-code sign-in.

A six-digit code is emailed and only its HMAC is stored. Codes expire after ten minutes, work
once, and lock after five wrong guesses. Requesting a code never reveals whether an account
exists, and a family account is created the first time a code is verified. Staff and admin
accounts exist only through the seed or admin tools.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.enums import UserRole
from app.models.identity import LoginCode, User
from app.security import crypto
from app.services import mail_service

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5

_CODE_CONTEXT = "login_code"
_IP_CONTEXT = "ip"
_DISPLAY_NAME_MAX = 80


class InvalidCode(AppError):
    """A wrong, expired, used or locked code. One message for all, so a guess learns nothing."""

    status_code = 401
    code = "invalid_code"
    message = "That code is not valid or has expired. Ask for a new one."


@dataclass(frozen=True)
class IssuedCode:
    """What ``request_code`` produced. Only tests look at it; callers must not return it."""

    email: str
    code: str | None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _code_hmac(email: str, code: str) -> str:
    # The code is bound to the address it was sent to, so it cannot be replayed for another.
    return crypto.lookup_hash(f"{email}|{code}", context=_CODE_CONTEXT)


def hash_ip(ip: str | None) -> str | None:
    return crypto.lookup_hash(ip, context=_IP_CONTEXT) if ip else None


def _now() -> datetime:
    return datetime.now(UTC)


def request_code(
    session: Session, email: str, *, ip: str | None = None, now: datetime | None = None
) -> IssuedCode:
    """Create and email a code for ``email``.

    Always behaves the same for the caller, whether or not an account exists. An account that
    exists but is switched off gets no code, silently. Earlier unused codes stop working.
    """
    email = normalize_email(email)
    now = now or _now()
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None and not user.is_active:
        logger.info("login code skipped for an inactive account")
        return IssuedCode(email, None)

    session.execute(
        update(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    session.add(
        LoginCode(
            email=email,
            code_hmac=_code_hmac(email, code),
            expires_at=now + CODE_TTL,
            ip_hmac=hash_ip(ip),
        )
    )
    session.commit()

    language = user.preferred_language if user else mail_service.DEFAULT_LANGUAGE
    try:
        mail_service.send(
            "login_code",
            email,
            language=language,
            code=code,
            minutes=int(CODE_TTL.total_seconds() // 60),
        )
    except mail_service.MailError as exc:
        # The caller still gets the same answer; the person can ask again.
        logger.error("login code email failed", extra={"reason": str(exc)})
    return IssuedCode(email, code)


def verify_code(
    session: Session, email: str, code: str, *, now: datetime | None = None
) -> tuple[User, bool]:
    """Check ``code`` and return ``(user, created)``. Raises ``InvalidCode`` otherwise.

    A wrong guess counts against the newest open code; the fifth locks it. A correct code is used
    up at once. A family account is created on first success.
    """
    email = normalize_email(email)
    now = now or _now()
    login = session.execute(
        select(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if login is None or login.expires_at <= now or login.attempts >= MAX_ATTEMPTS:
        raise InvalidCode()

    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    matches = crypto.hashes_match(login.code_hmac, _code_hmac(email, code.strip()))
    if not matches or (user is not None and not user.is_active):
        login.attempts += 1
        session.commit()
        raise InvalidCode()

    login.consumed_at = now
    created = user is None
    if user is None:
        user = User(
            email=email,
            display_name=_default_display_name(email),
            role=UserRole.FAMILY,
        )
        session.add(user)
    user.last_login_at = now
    session.commit()
    return user, created


def _default_display_name(email: str) -> str:
    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
    return (local.title() or "Family member")[:_DISPLAY_NAME_MAX]
