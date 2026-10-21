from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.models.enums import (
    ConsentActor,
    ConsentScope,
    ReviewResolution,
    ReviewSource,
    ReviewStatus,
)
from tests.factories import CaseFactory, ConsentFactory, ReviewItemFactory, UserFactory
from tests.models.helpers import assert_db_rejects


def test_consent_defaults(db_session) -> None:
    consent = ConsentFactory()
    db_session.refresh(consent)
    assert consent.withdrawn_at is None
    assert consent.ip_hmac is None
    assert consent.granted_at is not None
    assert consent.actor is ConsentActor.SELF


def test_consent_scope_and_actor_are_native_enums(db_session) -> None:
    ConsentFactory(scope=ConsentScope.AI_PROCESSING_ABROAD, actor=ConsentActor.GUARDIAN)
    row = db_session.execute(text("SELECT scope::text, actor::text FROM consents")).one()
    assert tuple(row) == ("ai_processing_abroad", "guardian")


def test_only_one_active_consent_per_case_and_scope(db_session) -> None:
    case = CaseFactory()
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    db_session.flush()
    error = assert_db_rejects(
        db_session, lambda: ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    )
    assert "uq_consents_case_scope_active" in str(error)


def test_a_scope_can_be_granted_again_after_withdrawal(db_session) -> None:
    case = CaseFactory()
    first = ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    db_session.flush()
    first.withdrawn_at = datetime.now(UTC)
    db_session.flush()
    second = ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    db_session.flush()
    assert case.active_consent(ConsentScope.PUBLIC_PAGE) is second
    assert len(case.consents) == 2


def test_different_scopes_and_cases_do_not_conflict(db_session) -> None:
    case = CaseFactory()
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    ConsentFactory(scope=ConsentScope.PUBLIC_PAGE)
    db_session.flush()
    assert {c.scope for c in case.consents} == {
        ConsentScope.PUBLIC_PAGE,
        ConsentScope.HOSPITAL_VERIFICATION,
    }


def test_consent_history_is_kept_when_several_withdrawals_exist(db_session) -> None:
    case = CaseFactory()
    for _ in range(3):
        consent = ConsentFactory(case=case, scope=ConsentScope.RECEIPT_SHARING)
        db_session.flush()
        consent.withdrawn_at = datetime.now(UTC)
        db_session.flush()
    assert len(case.consents) == 3


def test_active_consent_is_none_without_a_grant(db_session) -> None:
    assert CaseFactory().active_consent(ConsentScope.PUBLIC_PAGE) is None


def test_consent_records_who_granted_it(db_session) -> None:
    guardian = UserFactory()
    case = CaseFactory(is_minor=True)
    consent = ConsentFactory(case=case, granted_by_id=guardian.id, actor=ConsentActor.GUARDIAN)
    db_session.flush()
    assert consent.granted_by_id == guardian.id
    assert consent.wording_version == "v1"


def test_review_item_defaults(db_session) -> None:
    item = ReviewItemFactory()
    db_session.refresh(item)
    assert item.status is ReviewStatus.OPEN
    assert item.payload == {}
    assert item.resolution is None
    assert item.assigned_to_id is None
    assert item in item.case.review_items


def test_review_payload_round_trips(db_session) -> None:
    item = ReviewItemFactory(payload={"signals": ["duplicate_exact"], "weight": 60})
    db_session.flush()
    db_session.expire(item)
    assert item.payload == {"signals": ["duplicate_exact"], "weight": 60}


def _resolve(item, **overrides) -> None:
    resolver = overrides.pop("resolver", None) or UserFactory()
    values = {
        "status": ReviewStatus.RESOLVED,
        "resolution": ReviewResolution.APPROVED_FOR_CONFIRMATION,
        "resolution_note": "Checked the original with the hospital desk.",
        "resolved_by_id": resolver.id,
        "resolved_at": datetime.now(UTC),
    }
    values.update(overrides)
    for key, value in values.items():
        setattr(item, key, value)


def test_a_review_can_be_resolved_with_a_decision_and_note(db_session) -> None:
    item = ReviewItemFactory(source=ReviewSource.FAMILY_REQUEST)
    db_session.flush()
    _resolve(item)
    db_session.flush()
    assert item.status is ReviewStatus.RESOLVED


@pytest.mark.parametrize(
    "missing",
    [
        {"resolution": None},
        {"resolution_note": None},
        {"resolution_note": "   "},
        {"resolved_by_id": None},
        {"resolved_at": None},
    ],
)
def test_resolving_requires_decision_note_resolver_and_time(db_session, missing: dict) -> None:
    item = ReviewItemFactory()
    db_session.flush()
    _resolve(item, **missing)
    error = assert_db_rejects(db_session, lambda: None)
    assert "ck_review_items_resolved_has_decision" in str(error)


def test_an_open_review_needs_no_decision(db_session) -> None:
    assert ReviewItemFactory(status=ReviewStatus.OPEN).resolution is None


def test_a_case_with_review_items_cannot_be_deleted(db_session) -> None:
    item = ReviewItemFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(text("DELETE FROM cases WHERE id = :id"), {"id": item.case_id}),
    )
    assert "fk_review_items_case_id_cases" in str(error)
