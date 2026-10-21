"""Granting and withdrawing consent: wording versions, the guardian rule and the effects."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.errors import Conflict, NotFound, PermissionDenied
from app.models.case import Consent
from app.models.enums import CaseStatus, CloseReason, ConsentActor, ConsentScope
from app.models.platform import AuditLog
from app.security import crypto
from app.services import consent_service
from tests.factories import CaseFactory, ConsentFactory

HV, PP = ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE


def _grant(session, case, scope=PP, actor=ConsentActor.SELF, **overrides):  # type: ignore[no-untyped-def]
    values = {
        "scope": scope,
        "actor": actor,
        "wording_version": consent_service.current_version(scope, "en"),
        "language": "en",
    }
    return consent_service.grant(session, case, case.created_by, **{**values, **overrides})


def test_granting_records_who_what_when_and_in_which_wording(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    consent = _grant(db_session, case, ip="203.0.113.4")
    assert consent.scope == PP
    assert consent.actor == ConsentActor.SELF
    assert consent.granted_by_id == case.created_by_id
    assert (consent.wording_version, consent.language) == ("v1", "en")
    assert consent.withdrawn_at is None
    assert consent.granted_at <= datetime.now(UTC)
    assert consent.ip_hmac == crypto.hash_ip("203.0.113.4")
    assert case.active_consent(PP) is consent
    assert consent_service.has_active(case, PP)


def test_granting_is_audited_without_personal_data(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    _grant(db_session, case, HV, language="sw", wording_version="v1")
    [row] = db_session.execute(select(AuditLog)).scalars().all()
    assert row.action == "consent.granted"
    assert (row.target_type, row.target_id) == ("case", case.id)
    assert row.details == {
        "scope": "hospital_verification",
        "actor": "self",
        "wording_version": "v1",
        "language": "sw",
    }


def test_the_four_scopes_are_independent(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    for scope in ConsentScope:
        _grant(db_session, case, scope)
    assert {c.scope for c in case.consents} == set(ConsentScope)


def test_the_same_scope_cannot_be_granted_twice(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    _grant(db_session, case)
    with pytest.raises(Conflict) as raised:
        _grant(db_session, case)
    assert raised.value.code == "consent_already_granted"


def test_after_withdrawing_it_can_be_granted_again(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    _grant(db_session, case, ConsentScope.RECEIPT_SHARING)
    consent_service.withdraw(db_session, case, case.created_by, ConsentScope.RECEIPT_SHARING)
    again = _grant(db_session, case, ConsentScope.RECEIPT_SHARING)
    assert case.active_consent(ConsentScope.RECEIPT_SHARING) is again
    assert len(consent_service.list_for_case(case)) == 2


def test_an_old_wording_version_is_refused_with_the_current_one(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    with pytest.raises(Conflict) as raised:
        _grant(db_session, case, wording_version="v0")
    assert raised.value.code == "consent_wording_outdated"
    assert raised.value.details == {"current_version": "v1"}
    assert not db_session.execute(select(Consent)).first()


def test_a_minors_case_takes_only_guardian_consent(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(is_minor=True)
    with pytest.raises(Conflict) as raised:
        _grant(db_session, case, actor=ConsentActor.SELF)
    assert raised.value.code == "guardian_consent_required"
    assert _grant(db_session, case, actor=ConsentActor.GUARDIAN).actor == ConsentActor.GUARDIAN


def test_an_adult_may_also_consent_through_a_guardian(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(is_minor=False)
    assert _grant(db_session, case, actor=ConsentActor.GUARDIAN)


def test_an_administrator_cannot_consent_for_a_family(db_session) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PermissionDenied):
        _grant(db_session, CaseFactory(), actor=ConsentActor.ADMINISTRATOR)


@pytest.mark.parametrize("status", [CaseStatus.CLOSED, CaseStatus.CANCELLED, CaseStatus.FROZEN])
def test_a_finished_case_takes_no_new_consent(db_session, status: CaseStatus) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(
        status=status,
        close_reason=CloseReason.FULLY_SETTLED if status == CaseStatus.CLOSED else None,
        freeze_reason="under review" if status == CaseStatus.FROZEN else None,
    )
    with pytest.raises(Conflict) as raised:
        _grant(db_session, case)
    assert raised.value.code == "case_not_open"


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.DRAFT,
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
    ],
)
def test_an_open_case_takes_consent(db_session, status: CaseStatus) -> None:  # type: ignore[no-untyped-def]
    assert _grant(db_session, CaseFactory(status=status), ConsentScope.RECEIPT_SHARING)


# --- Withdrawal --------------------------------------------------------------------------------


def test_withdrawing_stamps_the_consent_and_keeps_the_record(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    consent = _grant(db_session, case, ConsentScope.AI_PROCESSING_ABROAD)
    result = consent_service.withdraw(
        db_session, case, case.created_by, ConsentScope.AI_PROCESSING_ABROAD
    )
    assert result is consent
    assert consent.withdrawn_at is not None
    assert case.active_consent(ConsentScope.AI_PROCESSING_ABROAD) is None
    assert case.status == CaseStatus.DRAFT


def test_withdrawing_something_never_given_is_not_found(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    with pytest.raises(NotFound) as raised:
        consent_service.withdraw(db_session, case, case.created_by, PP)
    assert raised.value.code == "consent_not_found"


def test_withdrawing_twice_is_not_found_the_second_time(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    _grant(db_session, case)
    consent_service.withdraw(db_session, case, case.created_by, PP)
    with pytest.raises(NotFound):
        consent_service.withdraw(db_session, case, case.created_by, PP)


def test_withdrawing_the_public_page_hides_a_live_case_at_once(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    _grant(db_session, case, PP)
    assert case.is_publicly_visible
    consent_service.withdraw(db_session, case, case.created_by, PP)
    assert not case.is_publicly_visible
    assert case.status == CaseStatus.LIVE


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.DRAFT,
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
    ],
)
def test_withdrawing_hospital_verification_before_confirmation_cancels_the_case(
    db_session, status: CaseStatus
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=status)
    _grant(db_session, case, HV)
    consent_service.withdraw(db_session, case, case.created_by, HV)
    assert case.status == CaseStatus.CANCELLED
    actions = {r.action: r for r in db_session.execute(select(AuditLog)).scalars()}
    assert actions["consent.withdrawn"].details == {
        "scope": "hospital_verification",
        "case_cancelled": True,
    }
    assert actions["case.transitioned"].details["reason"] == "consent_withdrawn"


@pytest.mark.parametrize("status", [CaseStatus.LIVE, CaseStatus.FUNDED])
def test_after_confirmation_withdrawing_hospital_verification_is_recorded_but_does_not_cancel(
    db_session, status: CaseStatus
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=status)
    ConsentFactory(case=case, scope=HV)
    consent_service.withdraw(db_session, case, case.created_by, HV)
    assert case.status == status
    [row] = db_session.execute(
        select(AuditLog).where(AuditLog.action == "consent.withdrawn")
    ).scalars()
    assert row.details["case_cancelled"] is False


def test_other_withdrawals_never_cancel(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.PENDING_CONFIRMATION)
    for scope in (PP, ConsentScope.AI_PROCESSING_ABROAD, ConsentScope.RECEIPT_SHARING):
        _grant(db_session, case, scope)
        consent_service.withdraw(db_session, case, case.created_by, scope)
    assert case.status == CaseStatus.PENDING_CONFIRMATION


def test_the_list_shows_active_and_withdrawn_newest_first(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    older = ConsentFactory(
        case=case,
        scope=PP,
        granted_at=datetime(2026, 10, 1, tzinfo=UTC),
        withdrawn_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    newer = ConsentFactory(case=case, scope=PP, granted_at=datetime(2026, 10, 3, tzinfo=UTC))
    assert consent_service.list_for_case(case) == [newer, older]
