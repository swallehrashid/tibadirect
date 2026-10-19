import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models.case import Case
from app.models.enums import CaseStatus, CloseReason, ConsentScope, TrustLevel
from tests.factories import CaseFactory, ConsentFactory, HospitalFactory, UserFactory
from tests.models.helpers import assert_db_rejects

PLAIN_REF = "IP/2026/00123"


def test_new_case_defaults(db_session) -> None:
    case = CaseFactory()
    db_session.refresh(case)
    assert case.status is CaseStatus.DRAFT
    assert case.trust_level is TrustLevel.UNVERIFIED
    assert case.is_estimate is True
    assert case.is_minor is False
    assert case.show_photo is False
    assert case.buffer_bps == 500
    assert case.target_kes_minor is None
    assert case.usd_goal_minor is None
    assert (case.pledged_kes_minor, case.settled_kes_minor, case.donor_count) == (0, 0, 0)
    assert (case.captured_net_usd_minor, case.settled_usd_minor, case.refunded_usd_minor) == (
        0,
        0,
        0,
    )
    assert case.version_id == 1
    assert case.close_reason is None


def test_public_slug_is_generated_and_unique(db_session) -> None:
    first, second = CaseFactory(), CaseFactory()
    assert re.fullmatch(r"[a-z2-7]{12}", first.public_slug)
    assert first.public_slug != second.public_slug
    error = assert_db_rejects(db_session, lambda: CaseFactory(public_slug=first.public_slug))
    assert "uq_cases_public_slug" in str(error)


def test_set_patient_ref_stores_ciphertext_and_a_lookup_hash(db_session) -> None:
    case = CaseFactory()
    case.set_patient_ref(f"  {PLAIN_REF} ")
    db_session.flush()
    assert case.patient_reference == PLAIN_REF
    assert re.fullmatch(r"[0-9a-f]{64}", case.patient_ref_hmac)


def test_patient_ref_hash_ignores_case_and_spacing(db_session) -> None:
    first, second = CaseFactory(), CaseFactory()
    first.set_patient_ref("IP/2026/00123")
    second.set_patient_ref("  ip/2026/00123 ")
    assert first.patient_ref_hmac == second.patient_ref_hmac


def test_a_blank_patient_ref_is_refused(db_session) -> None:
    with pytest.raises(ValueError, match="empty"):
        CaseFactory().set_patient_ref("   ")


def test_the_plain_patient_reference_is_in_no_column_and_no_repr(db_session) -> None:
    case = CaseFactory()
    case.set_patient_ref(PLAIN_REF)
    db_session.flush()
    whole_row = db_session.execute(
        text("SELECT to_jsonb(c)::text FROM cases c WHERE c.id = :id"), {"id": case.id}
    ).scalar_one()
    assert PLAIN_REF not in whole_row
    assert "00123" not in whole_row
    assert PLAIN_REF not in repr(case)
    assert case.alias not in repr(case)
    assert str(case.id) in repr(case)


def test_reference_matching_uses_the_hash_not_the_ciphertext(db_session) -> None:
    case = CaseFactory()
    case.set_patient_ref(PLAIN_REF)
    db_session.flush()
    probe = text("SELECT count(*) FROM cases WHERE patient_ref_hmac = :h")
    assert Case.__table__.c.patient_ref_hmac.type.length == 64
    found = db_session.execute(probe, {"h": case.patient_ref_hmac}).scalar_one()
    assert found == 1


@pytest.mark.parametrize("buffer_bps", [-1, 2001])
def test_buffer_must_stay_between_0_and_2000(db_session, buffer_bps: int) -> None:
    error = assert_db_rejects(db_session, lambda: CaseFactory(buffer_bps=buffer_bps))
    assert "ck_cases_buffer_range" in str(error)


@pytest.mark.parametrize("buffer_bps", [0, 2000])
def test_buffer_limits_are_accepted(db_session, buffer_bps: int) -> None:
    assert CaseFactory(buffer_bps=buffer_bps).buffer_bps == buffer_bps


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("target_kes_minor", "target_non_negative"),
        ("usd_goal_minor", "usd_goal_non_negative"),
        ("pledged_kes_minor", "pledged_non_negative"),
        ("settled_kes_minor", "settled_non_negative"),
        ("captured_net_usd_minor", "captured_net_non_negative"),
        ("settled_usd_minor", "settled_usd_non_negative"),
        ("refunded_usd_minor", "refunded_non_negative"),
    ],
)
def test_money_columns_cannot_go_negative(db_session, column: str, constraint: str) -> None:
    error = assert_db_rejects(db_session, lambda: CaseFactory(**{column: -1}))
    assert f"ck_cases_{constraint}" in str(error)


def test_a_closed_case_needs_a_close_reason(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: CaseFactory(status=CaseStatus.CLOSED))
    assert "ck_cases_closed_has_reason" in str(error)


def test_a_closed_case_with_a_reason_is_accepted(db_session) -> None:
    case = CaseFactory(status=CaseStatus.CLOSED, close_reason=CloseReason.FULLY_SETTLED)
    assert case.close_reason is CloseReason.FULLY_SETTLED


def test_a_frozen_case_needs_a_freeze_reason(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: CaseFactory(status=CaseStatus.FROZEN))
    assert "ck_cases_frozen_has_reason" in str(error)


def test_story_length_is_limited(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: CaseFactory(story="x" * 601))
    assert isinstance(error, DataError)


def test_status_is_a_lowercase_native_enum(db_session) -> None:
    CaseFactory(status=CaseStatus.PENDING_TRIAGE)
    stored = db_session.execute(text("SELECT status::text, trust_level::text FROM cases")).one()
    assert tuple(stored) == ("pending_triage", "unverified")


def test_optimistic_locking_rejects_a_stale_update(db_session) -> None:
    case = CaseFactory()
    db_session.flush()
    db_session.execute(
        text("UPDATE cases SET version_id = version_id + 1 WHERE id = :id"), {"id": case.id}
    )
    case.alias = "Changed"
    with pytest.raises(StaleDataError):
        db_session.flush()
    db_session.rollback()


def test_each_update_increments_the_version(db_session) -> None:
    case = CaseFactory()
    db_session.flush()
    assert case.version_id == 1
    case.alias = "Second"
    db_session.flush()
    assert case.version_id == 2


def test_indexes_exist_including_the_partial_one(db_session) -> None:
    rows = db_session.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'cases'")
    ).all()
    defs = dict(rows)
    assert "(hospital_id, status)" in defs["ix_cases_hospital_status"]
    partial = defs["ix_cases_active_patient_ref"]
    assert "patient_ref_hmac" in partial
    assert "WHERE" in partial
    assert "closed" in partial
    assert "cancelled" in partial
    assert "ix_cases_status" in defs
    assert "ix_cases_created_by_id" in defs
    assert "ix_cases_hospital_id" in defs


def test_case_links_to_its_owner_and_hospital_both_ways(db_session) -> None:
    case = CaseFactory()
    db_session.flush()
    assert case in case.created_by.cases
    assert case in case.hospital.cases


def test_a_hospital_or_user_with_cases_cannot_be_deleted(db_session) -> None:
    case = CaseFactory()
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: db_session.delete(case.hospital))
    assert isinstance(error, IntegrityError)


def test_a_user_with_cases_cannot_be_deleted(db_session) -> None:
    case = CaseFactory()
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: db_session.delete(case.created_by))
    assert isinstance(error, IntegrityError)


def test_is_publicly_visible_needs_a_public_status_and_active_consent(db_session) -> None:
    case = CaseFactory(status=CaseStatus.LIVE)
    assert case.is_publicly_visible is False  # no consent yet
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    db_session.flush()
    db_session.refresh(case)
    assert case.is_publicly_visible is True


@pytest.mark.parametrize(
    ("status", "visible"),
    [
        (CaseStatus.DRAFT, False),
        (CaseStatus.PENDING_TRIAGE, False),
        (CaseStatus.IN_REVIEW, False),
        (CaseStatus.PENDING_CONFIRMATION, False),
        (CaseStatus.LIVE, True),
        (CaseStatus.FUNDED, True),
        (CaseStatus.CLOSED, True),
        (CaseStatus.CANCELLED, False),
        (CaseStatus.FROZEN, False),
    ],
)
def test_visibility_by_status_with_consent(db_session, status, visible: bool) -> None:
    extra = {}
    if status is CaseStatus.CLOSED:
        extra["close_reason"] = CloseReason.FULLY_SETTLED
    if status is CaseStatus.FROZEN:
        extra["freeze_reason"] = "Under review"
    case = CaseFactory(status=status, **extra)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    db_session.flush()
    db_session.refresh(case)
    assert case.is_publicly_visible is visible


def test_a_withdrawn_public_page_consent_hides_the_page(db_session) -> None:
    from datetime import UTC, datetime

    case = CaseFactory(status=CaseStatus.LIVE)
    consent = ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    db_session.flush()
    db_session.refresh(case)
    assert case.is_publicly_visible is True
    consent.withdrawn_at = datetime.now(UTC)
    db_session.flush()
    assert case.active_consent(ConsentScope.PUBLIC_PAGE) is None
    assert case.is_publicly_visible is False


def test_other_scopes_do_not_make_a_page_public(db_session) -> None:
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    db_session.flush()
    db_session.refresh(case)
    assert case.is_publicly_visible is False


def test_two_owners_and_hospitals_stay_separate(db_session) -> None:
    first = CaseFactory(created_by=UserFactory(), hospital=HospitalFactory())
    second = CaseFactory(created_by=UserFactory(), hospital=HospitalFactory())
    db_session.flush()
    assert first.created_by.cases == [first]
    assert second.hospital.cases == [second]
