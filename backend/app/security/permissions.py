"""Who may do what: role decorators and the loaders that keep every case lookup in one place.

A route that takes a case ID must load the case through one of the loaders below, never with a
bare query, so the ownership rule cannot be forgotten. A case the caller may not see is reported
as not found: the API never confirms that someone else's case exists.
"""

import functools
import uuid
from collections.abc import Callable
from typing import Any

from flask_login import current_user
from sqlalchemy import exists, select

from app.errors import AuthenticationRequired, NotFound, PermissionDenied
from app.extensions import db
from app.models.case import Case
from app.models.enums import CaseStatus, UserRole
from app.models.hospital import HospitalStaff
from app.models.identity import User
from app.models.verification import Confirmation

# Statuses in which a hospital may see a case. Drafts and cases still being checked are the
# family's business until the bill reaches the hospital.
HOSPITAL_VISIBLE_STATUSES = frozenset(
    {
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
        CaseStatus.CLOSED,
        CaseStatus.FROZEN,
    }
)


def login_required[**P, R](view: Callable[P, R]) -> Callable[P, R]:
    """Reject anonymous callers with 401 and the standard error envelope."""

    @functools.wraps(view)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if not current_user.is_authenticated:
            raise AuthenticationRequired()
        return view(*args, **kwargs)

    return wrapper


def roles_required(*roles: UserRole) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Allow only signed-in users with one of ``roles``: 401 if anonymous, 403 otherwise.

    Hospital staff must also have an active staff profile at an active hospital.
    """

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not current_user.is_authenticated:
                raise AuthenticationRequired()
            user: User = current_user._get_current_object()  # type: ignore[attr-defined]
            if user.role not in roles:
                raise PermissionDenied()
            if user.role == UserRole.HOSPITAL_STAFF and active_staff_profile(user) is None:
                raise PermissionDenied("Your hospital account is not active.")
            return view(*args, **kwargs)

        return wrapper

    return decorator


def current_account() -> User:
    """The signed-in user as a real ``User`` (not the Flask-Login proxy)."""
    if not current_user.is_authenticated:
        raise AuthenticationRequired()
    return current_user._get_current_object()  # type: ignore[attr-defined, no-any-return]


def active_staff_profile(user: User) -> HospitalStaff | None:
    """The user's staff profile, only if it and its hospital are both active."""
    profile = user.staff_profile
    if profile is None or not profile.is_active or not profile.hospital.is_active:
        return None
    return profile


def current_staff() -> HospitalStaff:
    """The signed-in hospital staff member's active profile."""
    profile = active_staff_profile(current_account())
    if profile is None:
        raise PermissionDenied("Your hospital account is not active.")
    return profile


def _case_not_found() -> NotFound:
    return NotFound("Case not found.", code="case_not_found")


def get_owned_case_or_404(case_id: uuid.UUID, user: User) -> Case:
    """A family member's own case. Anyone else's case is a 404."""
    case = db.session.execute(
        select(Case).where(Case.id == case_id, Case.created_by_id == user.id)
    ).scalar_one_or_none()
    if case is None:
        raise _case_not_found()
    return case


def get_hospital_case_or_404(case_id: uuid.UUID, staff: HospitalStaff) -> Case:
    """A case at the staff member's own hospital, once the bill has reached the hospital.

    A cancelled case stays visible only if the hospital had already been asked to confirm it.
    """
    case = db.session.execute(
        select(Case).where(Case.id == case_id, Case.hospital_id == staff.hospital_id)
    ).scalar_one_or_none()
    if case is None or not _hospital_may_see(case):
        raise _case_not_found()
    return case


def _hospital_may_see(case: Case) -> bool:
    if case.status in HOSPITAL_VISIBLE_STATUSES:
        return True
    if case.status == CaseStatus.CANCELLED:
        return bool(
            db.session.execute(select(exists().where(Confirmation.case_id == case.id))).scalar_one()
        )
    return False


def get_case_for_admin_or_404(case_id: uuid.UUID) -> Case:
    """Any case. Only routes limited to admins may call this."""
    case = db.session.get(Case, case_id)
    if case is None:
        raise _case_not_found()
    return case


def get_case_for_user_or_404(case_id: uuid.UUID, user: User) -> Case:
    """The case as ``user`` may see it: own (family), their hospital's (staff), any (admin)."""
    if user.role == UserRole.ADMIN:
        return get_case_for_admin_or_404(case_id)
    if user.role == UserRole.HOSPITAL_STAFF:
        profile = active_staff_profile(user)
        if profile is None:
            raise PermissionDenied("Your hospital account is not active.")
        return get_hospital_case_or_404(case_id, profile)
    return get_owned_case_or_404(case_id, user)


def user_loader(user_id: str) -> User | None:
    """Flask-Login hook: the user for a session, unless the account has been switched off."""
    try:
        identifier = uuid.UUID(user_id)
    except ValueError:
        return None
    user = db.session.get(User, identifier)
    return user if user is not None and user.is_active else None


def unauthorized() -> Any:
    """Flask-Login hook: anonymous access to a protected view is a 401 with the standard body."""
    raise AuthenticationRequired()
