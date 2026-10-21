"""The case state machine: every allowed move works and every other move is refused."""

from datetime import UTC, datetime
from itertools import product

import pytest
from sqlalchemy import select

from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    ConsentScope,
    TrustLevel,
    UserRole,
)
from app.models.platform import AuditLog
from app.services import case_service
from app.services.case_service import ALLOWED_TRANSITIONS, InvalidTransition, transition
from tests.factories import CaseFactory, ConsentFactory, UserFactory

S = CaseStatus


def _case_in(status: CaseStatus, *, public_consent: bool = True):  # type: ignore[no-untyped-def]
    case = CaseFactory(
        status=status,
        close_reason=CloseReason.FULLY_SETTLED if status == S.CLOSED else None,
        freeze_reason="under review" if status == S.FROZEN else None,
    )
    if public_consent:
        ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    return case


def _kwargs(to: CaseStatus) -> dict:
    if to == S.CLOSED:
        return {"close_reason": CloseReason.FULLY_SETTLED}
    if to == S.FROZEN:
        return {"reason": "suspected fraud"}
    return {}


ALL_PAIRS = list(product(S, S))
ALLOWED = [(a, b) for a, b in ALL_PAIRS if b in ALLOWED_TRANSITIONS[a]]
FORBIDDEN = [(a, b) for a, b in ALL_PAIRS if b not in ALLOWED_TRANSITIONS[a]]


def test_the_table_matches_the_design_diagram() -> None:
    assert sum(len(targets) for targets in ALLOWED_TRANSITIONS.values()) == len(ALLOWED)
    assert ALLOWED_TRANSITIONS[S.CLOSED] == frozenset()
    assert ALLOWED_TRANSITIONS[S.CANCELLED] == frozenset()
    assert ALLOWED_TRANSITIONS[S.FROZEN] == {S.CANCELLED}
    assert S.LIVE in ALLOWED_TRANSITIONS[S.PENDING_CONFIRMATION]
    assert S.LIVE not in ALLOWED_TRANSITIONS[S.DRAFT]
    assert S.LIVE not in ALLOWED_TRANSITIONS[S.PENDING_TRIAGE]
    assert S.FUNDED not in ALLOWED_TRANSITIONS[S.PENDING_CONFIRMATION]


@pytest.mark.parametrize(("source", "target"), ALLOWED, ids=lambda s: s.value)
def test_every_allowed_move_works(db_session, source: CaseStatus, target: CaseStatus) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(source)
    transition(case, target, UserFactory(), **_kwargs(target))
    assert case.status == target
    assert case_service.can_transition(source, target)


@pytest.mark.parametrize(("source", "target"), FORBIDDEN, ids=lambda s: s.value)
def test_every_other_move_is_refused_and_changes_nothing(
    db_session, source: CaseStatus, target: CaseStatus
) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(source)
    with pytest.raises(InvalidTransition) as raised:
        transition(case, target, UserFactory(), **_kwargs(target))
    assert raised.value.status_code == 409
    assert raised.value.code == "invalid_transition"
    assert raised.value.details == {"from": source.value, "to": target.value}
    assert case.status == source
    assert not db_session.execute(select(AuditLog)).first()


# --- Trust level and dates ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "target", "trust"),
    [
        (S.DRAFT, S.PENDING_TRIAGE, TrustLevel.UNVERIFIED),
        (S.PENDING_TRIAGE, S.IN_REVIEW, TrustLevel.UNVERIFIED),
        (S.PENDING_TRIAGE, S.PENDING_CONFIRMATION, TrustLevel.PENDING_CONFIRMATION),
        (S.IN_REVIEW, S.PENDING_CONFIRMATION, TrustLevel.PENDING_CONFIRMATION),
        (S.PENDING_CONFIRMATION, S.IN_REVIEW, TrustLevel.UNVERIFIED),
        (S.PENDING_CONFIRMATION, S.LIVE, TrustLevel.HOSPITAL_CONFIRMED),
        (S.LIVE, S.FUNDED, TrustLevel.HOSPITAL_CONFIRMED),
        (S.FUNDED, S.LIVE, TrustLevel.HOSPITAL_CONFIRMED),
        (S.LIVE, S.CLOSED, TrustLevel.HOSPITAL_CONFIRMED),
        (S.LIVE, S.FROZEN, TrustLevel.UNVERIFIED),
        (S.PENDING_CONFIRMATION, S.FROZEN, TrustLevel.UNVERIFIED),
    ],
)
def test_trust_follows_status(db_session, source, target, trust) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(source)
    case.trust_level = (
        TrustLevel.HOSPITAL_CONFIRMED
        if source in {S.LIVE, S.FUNDED}
        else TrustLevel.PENDING_CONFIRMATION
    )
    transition(case, target, UserFactory(), **_kwargs(target))
    assert case.trust_level == trust


def test_a_frozen_case_never_becomes_hospital_confirmed_again(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.LIVE)
    case.trust_level = TrustLevel.HOSPITAL_CONFIRMED
    transition(case, S.FROZEN, UserFactory(role=UserRole.ADMIN), "suspected fraud")
    assert case.trust_level == TrustLevel.UNVERIFIED
    transition(case, S.CANCELLED, ActorType.SYSTEM, "refunds completed")
    assert case.trust_level == TrustLevel.UNVERIFIED


def test_cancelling_a_confirmed_case_keeps_its_trust_level(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.LIVE)
    case.trust_level = TrustLevel.HOSPITAL_CONFIRMED
    transition(case, S.CANCELLED, UserFactory(), "treatment_cancelled")
    assert case.trust_level == TrustLevel.HOSPITAL_CONFIRMED


def test_timestamps_are_set_by_the_move_that_earns_them(db_session) -> None:  # type: ignore[no-untyped-def]
    when = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
    case = _case_in(S.DRAFT)
    transition(case, S.PENDING_TRIAGE, UserFactory(), now=when)
    assert case.submitted_at == when
    transition(case, S.PENDING_CONFIRMATION, ActorType.SYSTEM, now=when)
    assert case.live_at is None
    transition(case, S.LIVE, ActorType.SYSTEM, now=when)
    assert case.live_at == when
    transition(case, S.FUNDED, ActorType.SYSTEM, now=when)
    assert case.funded_at == when
    transition(case, S.LIVE, ActorType.SYSTEM, now=datetime(2026, 11, 1, tzinfo=UTC))
    assert case.funded_at is None
    assert case.live_at == when  # going back to live does not restart the clock


def test_closing_records_when_and_why(db_session) -> None:  # type: ignore[no-untyped-def]
    when = datetime(2026, 10, 21, tzinfo=UTC)
    case = _case_in(S.FUNDED)
    transition(case, S.CLOSED, ActorType.SYSTEM, close_reason=CloseReason.FULLY_SETTLED, now=when)
    assert (case.closed_at, case.close_reason) == (when, CloseReason.FULLY_SETTLED)


def test_closing_without_a_reason_is_refused(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.LIVE)
    with pytest.raises(InvalidTransition) as raised:
        transition(case, S.CLOSED, ActorType.SYSTEM)
    assert raised.value.code == "close_reason_required"
    assert case.status == S.LIVE


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_freezing_needs_a_written_reason(db_session, reason) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.LIVE)
    with pytest.raises(InvalidTransition) as raised:
        transition(case, S.FROZEN, UserFactory(role=UserRole.ADMIN), reason)
    assert raised.value.code == "freeze_reason_required"


def test_freezing_stores_the_reason_and_time(db_session) -> None:  # type: ignore[no-untyped-def]
    when = datetime(2026, 10, 21, tzinfo=UTC)
    case = _case_in(S.LIVE)
    transition(case, S.FROZEN, UserFactory(role=UserRole.ADMIN), "  looks forged  ", now=when)
    assert (case.frozen_at, case.freeze_reason) == (when, "looks forged")


# --- The public page consent -------------------------------------------------------------------


def test_a_case_cannot_go_live_without_the_public_page_consent(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.PENDING_CONFIRMATION, public_consent=False)
    with pytest.raises(InvalidTransition) as raised:
        transition(case, S.LIVE, ActorType.SYSTEM)
    assert raised.value.code == "public_consent_required"
    assert case.status == S.PENDING_CONFIRMATION


def test_a_withdrawn_public_page_consent_does_not_count(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.PENDING_CONFIRMATION, public_consent=False)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE, withdrawn_at=datetime.now(UTC))
    with pytest.raises(InvalidTransition):
        transition(case, S.LIVE, ActorType.SYSTEM)


def test_other_consents_do_not_satisfy_the_public_page_rule(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.PENDING_CONFIRMATION, public_consent=False)
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    with pytest.raises(InvalidTransition):
        transition(case, S.LIVE, ActorType.SYSTEM)


def test_a_funded_case_can_return_to_live_even_if_the_page_was_withdrawn(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.FUNDED, public_consent=False)
    transition(case, S.LIVE, ActorType.SYSTEM)
    assert case.status == S.LIVE


# --- Audit -------------------------------------------------------------------------------------


def test_every_move_is_audited_with_both_ends_and_the_actor(db_session) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory()
    case = _case_in(S.DRAFT)
    transition(case, S.PENDING_TRIAGE, user)
    transition(case, S.IN_REVIEW, ActorType.SYSTEM, "triage flagged a duplicate")
    rows = (
        db_session.execute(select(AuditLog).order_by(AuditLog.created_at, AuditLog.action))
        .scalars()
        .all()
    )
    assert {r.action for r in rows} == {"case.transitioned"}
    details = sorted((r.details["from"], r.details["to"]) for r in rows)
    assert details == [("draft", "pending_triage"), ("pending_triage", "in_review")]
    by_to = {r.details["to"]: r for r in rows}
    assert by_to["pending_triage"].actor_user_id == user.id
    assert by_to["in_review"].actor_type == ActorType.SYSTEM
    assert by_to["in_review"].details["reason"] == "triage flagged a duplicate"
    assert all(r.target_id == case.id and r.target_type == "case" for r in rows)


def test_a_freeze_reason_is_stored_on_the_case_not_copied_into_the_audit_details(
    db_session,
) -> None:  # type: ignore[no-untyped-def]
    case = _case_in(S.LIVE)
    transition(case, S.FROZEN, UserFactory(role=UserRole.ADMIN), "looks forged")
    [row] = db_session.execute(select(AuditLog)).scalars().all()
    assert "reason" not in row.details
    assert case.freeze_reason == "looks forged"


def test_an_approved_agent_move_names_its_approver(db_session) -> None:  # type: ignore[no-untyped-def]
    admin = UserFactory(role=UserRole.ADMIN)
    case = _case_in(S.LIVE)
    transition(case, S.FROZEN, ActorType.AGENT, "agent flagged it", approver=admin)
    [row] = db_session.execute(select(AuditLog)).scalars().all()
    assert (row.actor_type, row.actor_user_id) == (ActorType.AGENT, admin.id)
