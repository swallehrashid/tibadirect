"""Role gates and the case loaders: every combination of caller and case."""

import uuid
from collections.abc import Callable

import pytest

from app.models.enums import CaseStatus, CloseReason, UserRole
from app.security import permissions
from tests.browser import Browser
from tests.factories import (
    CaseFactory,
    ConfirmationFactory,
    HospitalFactory,
    HospitalStaffFactory,
    UserFactory,
)

BrowserFor = Callable[..., Browser]
GATES = "/api/_test/secured"


def _error(response) -> dict:  # type: ignore[no-untyped-def]
    return response.get_json()["error"]


# --- Role decorators ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["anyone", "family", "staff", "family-or-admin"])
def test_anonymous_callers_get_401(browser: Browser, path: str) -> None:
    response = browser.get(f"{GATES}/{path}")
    assert response.status_code == 401
    assert _error(response)["code"] == "unauthenticated"


def test_anonymous_callers_get_401_before_validation_or_csrf_on_writes(browser: Browser) -> None:
    assert browser.post(f"{GATES}/admin").status_code == 401


def test_any_signed_in_user_passes_the_login_gate(browser_for: BrowserFor) -> None:
    user = UserFactory(email="a@example.test")
    assert browser_for(user).get(f"{GATES}/anyone").get_json() == {"user": "a@example.test"}


@pytest.mark.parametrize(
    ("role", "path", "method", "allowed"),
    [
        (UserRole.FAMILY, "family", "get", True),
        (UserRole.FAMILY, "admin", "post", False),
        (UserRole.FAMILY, "staff", "get", False),
        (UserRole.ADMIN, "admin", "post", True),
        (UserRole.ADMIN, "family", "get", False),
        (UserRole.ADMIN, "staff", "get", False),
        (UserRole.FAMILY, "family-or-admin", "get", True),
        (UserRole.ADMIN, "family-or-admin", "get", True),
        (UserRole.HOSPITAL_STAFF, "family-or-admin", "get", False),
    ],
)
def test_role_gate_matrix(
    browser_for: BrowserFor, role: UserRole, path: str, method: str, allowed: bool
) -> None:
    user = UserFactory(role=role)
    if role == UserRole.HOSPITAL_STAFF:
        HospitalStaffFactory(user=user)
    response = getattr(browser_for(user), method)(f"{GATES}/{path}")
    if allowed:
        assert response.status_code == 200
    else:
        assert response.status_code == 403
        assert _error(response)["code"] == "forbidden"


def test_staff_pass_the_staff_gate_and_see_their_own_hospital(browser_for: BrowserFor) -> None:
    staff = HospitalStaffFactory()
    response = browser_for(staff.user).get(f"{GATES}/staff")
    assert response.get_json() == {"hospital_id": str(staff.hospital_id)}


def test_a_staff_user_without_a_profile_is_refused(browser_for: BrowserFor) -> None:
    orphan = UserFactory(role=UserRole.HOSPITAL_STAFF)
    assert browser_for(orphan).get(f"{GATES}/staff").status_code == 403


def test_inactive_staff_are_refused(browser_for: BrowserFor) -> None:
    staff = HospitalStaffFactory(is_active=False)
    assert browser_for(staff.user).get(f"{GATES}/staff").status_code == 403


def test_staff_of_an_inactive_hospital_are_refused(browser_for: BrowserFor) -> None:
    staff = HospitalStaffFactory(hospital=HospitalFactory(is_active=False))
    assert browser_for(staff.user).get(f"{GATES}/staff").status_code == 403


def test_unauthenticated_vs_forbidden_codes_are_distinct(browser_for: BrowserFor) -> None:
    response = browser_for(UserFactory()).post(f"{GATES}/admin")
    assert response.status_code == 403
    assert _error(response)["code"] == "forbidden"


# --- Loaders -----------------------------------------------------------------------------------


def test_owner_loads_own_case_and_not_anyone_elses(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    other = UserFactory()
    assert permissions.get_owned_case_or_404(case.id, case.created_by).id == case.id
    with pytest.raises(permissions.NotFound) as raised:
        permissions.get_owned_case_or_404(case.id, other)
    assert raised.value.code == "case_not_found"


def test_a_missing_case_is_indistinguishable_from_someone_elses(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    other = UserFactory()
    with pytest.raises(permissions.NotFound) as theirs:
        permissions.get_owned_case_or_404(case.id, other)
    with pytest.raises(permissions.NotFound) as missing:
        permissions.get_owned_case_or_404(uuid.uuid4(), other)
    assert (theirs.value.message, theirs.value.code, theirs.value.status_code) == (
        missing.value.message,
        missing.value.code,
        missing.value.status_code,
    )


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
        CaseStatus.CLOSED,
        CaseStatus.FROZEN,
    ],
)
def test_staff_see_their_hospitals_cases_once_the_bill_reached_them(
    db_session, status: CaseStatus
) -> None:  # type: ignore[no-untyped-def]
    staff = HospitalStaffFactory()
    case = CaseFactory(
        hospital=staff.hospital,
        status=status,
        freeze_reason="fraud suspicion",
        close_reason=CloseReason.FULLY_SETTLED,
    )
    assert permissions.get_hospital_case_or_404(case.id, staff).id == case.id


@pytest.mark.parametrize(
    "status", [CaseStatus.DRAFT, CaseStatus.PENDING_TRIAGE, CaseStatus.IN_REVIEW]
)
def test_staff_do_not_see_cases_that_have_not_reached_the_hospital(
    db_session, status: CaseStatus
) -> None:  # type: ignore[no-untyped-def]
    staff = HospitalStaffFactory()
    case = CaseFactory(hospital=staff.hospital, status=status)
    with pytest.raises(permissions.NotFound):
        permissions.get_hospital_case_or_404(case.id, staff)


def test_staff_never_see_another_hospitals_case(db_session) -> None:  # type: ignore[no-untyped-def]
    mine = HospitalStaffFactory()
    case = CaseFactory(status=CaseStatus.LIVE)
    assert case.hospital_id != mine.hospital_id
    with pytest.raises(permissions.NotFound):
        permissions.get_hospital_case_or_404(case.id, mine)


@pytest.mark.parametrize("status", [CaseStatus.IN_REVIEW, CaseStatus.CANCELLED])
def test_a_case_in_review_or_cancelled_stays_visible_only_after_the_hospital_decided(
    db_session, status: CaseStatus
) -> None:  # type: ignore[no-untyped-def]
    staff = HospitalStaffFactory()
    undecided = CaseFactory(hospital=staff.hospital, status=status)
    decided = CaseFactory(hospital=staff.hospital, status=status)
    ConfirmationFactory(case=decided, confirmation_request__requested_by=staff)
    with pytest.raises(permissions.NotFound):
        permissions.get_hospital_case_or_404(undecided.id, staff)
    assert permissions.get_hospital_case_or_404(decided.id, staff).id == decided.id


def test_a_cancelled_case_is_visible_only_if_the_hospital_had_been_asked(db_session) -> None:  # type: ignore[no-untyped-def]
    staff = HospitalStaffFactory()
    never_asked = CaseFactory(hospital=staff.hospital, status=CaseStatus.CANCELLED)
    asked = CaseFactory(hospital=staff.hospital, status=CaseStatus.CANCELLED)
    ConfirmationFactory(case=asked, confirmation_request__requested_by=staff)
    with pytest.raises(permissions.NotFound):
        permissions.get_hospital_case_or_404(never_asked.id, staff)
    assert permissions.get_hospital_case_or_404(asked.id, staff).id == asked.id


def test_admin_loads_any_case(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    assert permissions.get_case_for_admin_or_404(case.id).id == case.id
    with pytest.raises(permissions.NotFound):
        permissions.get_case_for_admin_or_404(uuid.uuid4())


def test_user_loader_ignores_garbage_missing_and_switched_off_users(db_session) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory()
    assert permissions.user_loader(str(user.id)).id == user.id
    assert permissions.user_loader("not-a-uuid") is None
    assert permissions.user_loader(str(uuid.uuid4())) is None
    user.is_active = False
    db_session.flush()
    assert permissions.user_loader(str(user.id)) is None


# --- IDOR across HTTP --------------------------------------------------------------------------


def test_the_dispatching_loader_enforces_each_roles_view(browser_for: BrowserFor) -> None:
    staff = HospitalStaffFactory()
    mine = CaseFactory(hospital=staff.hospital, status=CaseStatus.LIVE)
    theirs = CaseFactory(status=CaseStatus.LIVE)
    draft = CaseFactory(hospital=staff.hospital)
    admin = UserFactory(role=UserRole.ADMIN)

    owner_browser = browser_for(mine.created_by)
    assert owner_browser.get(f"{GATES}/cases/{mine.id}").status_code == 200
    assert owner_browser.get(f"{GATES}/cases/{theirs.id}").status_code == 404

    staff_browser = browser_for(staff.user)
    assert staff_browser.get(f"{GATES}/cases/{mine.id}").status_code == 200
    assert staff_browser.get(f"{GATES}/cases/{theirs.id}").status_code == 404
    assert staff_browser.get(f"{GATES}/cases/{draft.id}").status_code == 404

    admin_browser = browser_for(admin)
    for case in (mine, theirs, draft):
        assert admin_browser.get(f"{GATES}/cases/{case.id}").status_code == 200


def test_someone_elses_case_and_a_missing_one_give_the_same_answer(browser_for: BrowserFor) -> None:
    case = CaseFactory()
    stranger = browser_for(UserFactory())
    theirs = stranger.get(f"{GATES}/cases/{case.id}")
    missing = stranger.get(f"{GATES}/cases/{uuid.uuid4()}")
    assert theirs.status_code == missing.status_code == 404
    assert _error(theirs)["code"] == _error(missing)["code"] == "case_not_found"
    assert _error(theirs)["message"] == _error(missing)["message"]
