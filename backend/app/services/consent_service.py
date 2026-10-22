"""Layered consent: separate, versioned, provable and withdrawable.

The wording families agree to lives in ``app/consent_texts/<scope>.<lang>.md`` (a ``version`` in
the front matter, a ``# Title`` and the text). A consent records which version of which wording was
shown, in which language, by whom and when. A minor's case accepts only guardian consents.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.errors import Conflict, NotFound, PermissionDenied
from app.models.case import Case, Consent
from app.models.enums import CaseStatus, ConsentActor, ConsentScope
from app.models.identity import User
from app.security import crypto
from app.services import audit_service, case_service, confirmation_service

TEXTS_DIR = Path(__file__).resolve().parents[1] / "consent_texts"
LANGUAGES = ("en", "sw")
DEFAULT_LANGUAGE = "en"

# Who may give consent through the family API. Administrators never consent for a family.
FAMILY_ACTORS = frozenset({ConsentActor.SELF, ConsentActor.GUARDIAN})

_FRONT_MATTER = re.compile(r"\A---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class ConsentText:
    scope: ConsentScope
    language: str
    version: str
    title: str
    body: str
    reviewed: bool


class ConsentTextMissing(RuntimeError):
    """A scope has no wording in some language: a packaging bug, found by the tests."""


def _parse(path: Path, scope: ConsentScope, language: str) -> ConsentText:
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if match is None:
        raise ConsentTextMissing(f"{path.name} has no front matter")
    meta = dict(line.split(":", 1) for line in match["meta"].splitlines() if ":" in line)
    meta = {key.strip(): value.strip() for key, value in meta.items()}
    heading, _, body = match["body"].strip().partition("\n")
    if not heading.startswith("# ") or "version" not in meta:
        raise ConsentTextMissing(f"{path.name} needs a version and a '# Title' line")
    return ConsentText(
        scope=scope,
        language=language,
        version=meta["version"],
        title=heading[2:].strip(),
        body=body.strip(),
        reviewed=meta.get("reviewed", "false") == "true",
    )


@cache
def load_texts() -> dict[tuple[ConsentScope, str], ConsentText]:
    """Every wording, read once. Raises if any scope is missing a language."""
    texts = {}
    for scope in ConsentScope:
        for language in LANGUAGES:
            path = TEXTS_DIR / f"{scope.value}.{language}.md"
            if not path.exists():
                raise ConsentTextMissing(f"No {language} wording for {scope.value}")
            texts[(scope, language)] = _parse(path, scope, language)
    return texts


def current_texts(language: str) -> list[ConsentText]:
    """The current wording of every scope in ``language``, in scope order."""
    return [load_texts()[(scope, language)] for scope in ConsentScope]


def current_version(scope: ConsentScope, language: str) -> str:
    return load_texts()[(scope, language)].version


def has_active(case: Case, scope: ConsentScope) -> bool:
    return case.active_consent(scope) is not None


def list_for_case(case: Case) -> list[Consent]:
    """Active and withdrawn consents, newest first."""
    return sorted(case.consents, key=lambda consent: consent.granted_at, reverse=True)


def grant(
    session: Session,
    case: Case,
    user: User,
    *,
    scope: ConsentScope,
    actor: ConsentActor,
    wording_version: str,
    language: str,
    ip: str | None = None,
) -> Consent:
    """Record that ``user`` agreed to the current wording of ``scope``. Caller commits."""
    if case.status not in case_service.OPEN_STATUSES:
        raise Conflict("This case no longer accepts new consents.", code="case_not_open")
    if actor not in FAMILY_ACTORS:
        raise PermissionDenied("Only the family or a guardian can give consent here.")
    if case.is_minor and actor != ConsentActor.GUARDIAN:
        raise Conflict(
            "For a child, a parent or legal guardian must give consent.",
            code="guardian_consent_required",
        )
    if wording_version != current_version(scope, language):
        raise Conflict(
            "The wording has changed. Show the current text and ask again.",
            code="consent_wording_outdated",
            details={"current_version": current_version(scope, language)},
        )
    if has_active(case, scope):
        raise Conflict("This consent has already been given.", code="consent_already_granted")

    consent = Consent(
        case=case,
        scope=scope,
        actor=actor,
        granted_by_id=user.id,
        wording_version=wording_version,
        language=language,
        ip_hmac=crypto.hash_ip(ip),
        granted_at=datetime.now(UTC),
    )
    session.add(consent)
    session.flush()
    audit_service.record(
        "consent.granted",
        case,
        actor=user,
        details={
            "scope": scope,
            "actor": actor,
            "wording_version": wording_version,
            "language": language,
        },
    )
    if scope == ConsentScope.PUBLIC_PAGE:
        # The hospital may have confirmed while the page consent was missing.
        confirmation_service.go_live_if_ready(session, case)
    return consent


def withdraw(session: Session, case: Case, user: User, scope: ConsentScope) -> Consent:
    """Withdraw the active consent for ``scope``. Caller commits.

    ``public_page`` hides the page at once, because visibility needs an active consent.
    ``hospital_verification`` before the hospital has confirmed cancels the case: without it
    the hospital cannot be asked, and there is nothing to raise money for.
    """
    consent = case.active_consent(scope)
    if consent is None:
        raise NotFound("There is no active consent for that.", code="consent_not_found")
    consent.withdrawn_at = datetime.now(UTC)
    session.flush()
    cancelled = (
        scope == ConsentScope.HOSPITAL_VERIFICATION
        and case.status in case_service.UNCONFIRMED_STATUSES
    )
    audit_service.record(
        "consent.withdrawn",
        case,
        actor=user,
        details={"scope": scope, "case_cancelled": cancelled},
    )
    if cancelled:
        case_service.transition(case, CaseStatus.CANCELLED, user, "consent_withdrawn")
    return consent
