"""Creating, editing and deleting draft cases, and the rules that keep a case's words safe."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.errors import Conflict, ValidationFailed
from app.models.case import Case
from app.models.enums import (
    CaseStatus,
    ConsentActor,
    ConsentScope,
    DonationStatus,
    SettlementStatus,
)
from app.models.platform import AuditLog
from app.security import crypto
from app.services import case_service
from tests.factories import (
    CaseFactory,
    ConsentFactory,
    DonationFactory,
    FxRateFactory,
    HospitalFactory,
    SettlementFactory,
    UserFactory,
)


def _create(session, user, hospital=None, **overrides) -> Case:  # type: ignore[no-untyped-def]
    values = {
        "hospital_id": (hospital or HospitalFactory()).id,
        "alias": "Baby Zawadi",
        "region": "Nairobi",
        "patient_reference": "ADM-2026/0412",
    }
    return case_service.create_draft(session, user, **{**values, **overrides})


def _invalid(call) -> ValidationFailed:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationFailed) as raised:
        call()
    return raised.value


# --- Creating ----------------------------------------------------------------------------------


def test_a_new_draft_belongs_to_its_creator_and_starts_unverified(app, db_session) -> None:  # type: ignore[no-untyped-def]
    user, hospital = UserFactory(), HospitalFactory()
    case = _create(db_session, user, hospital)
    assert case.status == CaseStatus.DRAFT
    assert case.created_by_id == user.id
    assert case.hospital_id == hospital.id
    assert (case.alias, case.region) == ("Baby Zawadi", "Nairobi")
    assert case.trust_level.value == "unverified"
    assert case.buffer_bps == app.config["BUFFER_BPS_DEFAULT"]
    assert len(case.public_slug) == 12
    assert case.is_minor is False
    assert case.show_photo is False


def test_the_patient_reference_is_encrypted_and_hashed(db_session) -> None:  # type: ignore[no-untyped-def]
    case = _create(db_session, UserFactory(), patient_reference="  adm-2026/0412 ")
    assert case.patient_reference == "adm-2026/0412"
    assert b"2026" not in bytes(case.patient_ref_enc)[12:]  # ciphertext, not text
    assert case.patient_ref_hmac == crypto.lookup_hash("adm-2026/0412", context="patient_ref")


def test_creation_is_audited_with_ids_only(db_session) -> None:  # type: ignore[no-untyped-def]
    hospital = HospitalFactory()
    user = UserFactory()
    case = _create(db_session, user, hospital)
    [row] = db_session.execute(select(AuditLog)).scalars()
    assert row.action == "case.created"
    assert (row.target_id, row.actor_user_id) == (case.id, user.id)
    assert row.details == {"hospital_id": str(hospital.id)}


def test_an_unknown_or_inactive_hospital_is_refused(db_session) -> None:  # type: ignore[no-untyped-def]
    for hospital_id in (uuid.uuid4(), HospitalFactory(is_active=False).id):
        error = _invalid(
            lambda hid=hospital_id: _create(db_session, UserFactory(), hospital_id=hid)
        )
        assert error.code == "hospital_not_found"
        assert "hospital_id" in error.details["json"]


@pytest.mark.parametrize(
    ("alias", "code"),
    [
        ("A", "alias_length"),
        ("x" * 41, "alias_length"),
        ("   ", "alias_length"),
        ("Call 0712 345 678", "alias_personal_data"),
        ("mama@example.test", "alias_personal_data"),
        ("Baby with cancer", "alias_medical_terms"),
    ],
)
def test_bad_aliases_are_refused_with_a_specific_code(db_session, alias: str, code: str) -> None:  # type: ignore[no-untyped-def]
    error = _invalid(lambda: _create(db_session, UserFactory(), alias=alias))
    assert error.code == code
    assert "alias" in error.details["json"]


def test_an_alias_naming_an_illness_reports_which_words(db_session) -> None:  # type: ignore[no-untyped-def]
    error = _invalid(lambda: _create(db_session, UserFactory(), alias="Brave malaria fighter"))
    assert error.details["terms"] == ["malaria"]


def test_the_alias_is_tidied(db_session) -> None:  # type: ignore[no-untyped-def]
    assert _create(db_session, UserFactory(), alias="  Baby    Zawadi ").alias == "Baby Zawadi"


@pytest.mark.parametrize("region", ["", "N", "x" * 41, "Nairobi 123", "<script>", "12345"])
def test_bad_regions_are_refused(db_session, region: str) -> None:  # type: ignore[no-untyped-def]
    assert (
        _invalid(lambda: _create(db_session, UserFactory(), region=region)).code == "region_invalid"
    )


@pytest.mark.parametrize(
    "region", ["Nairobi", "Taita Taveta", "Murang'a", "Tharaka-Nithi", "Kisumu Town"]
)
def test_ordinary_regions_are_accepted(db_session, region: str) -> None:  # type: ignore[no-untyped-def]
    assert _create(db_session, UserFactory(), region=region).region == region


@pytest.mark.parametrize(
    "reference", ["", "AB", "x" * 61, "-starts-bad", "<b>1</b>", "ref;drop table"]
)
def test_bad_patient_references_are_refused(db_session, reference: str) -> None:  # type: ignore[no-untyped-def]
    error = _invalid(lambda: _create(db_session, UserFactory(), patient_reference=reference))
    assert error.code == "patient_reference_invalid"


@pytest.mark.parametrize("reference", ["ADM-2026/0412", "IP 00123", "A.12_34#5", "123456"])
def test_ordinary_patient_references_are_accepted(db_session, reference: str) -> None:  # type: ignore[no-untyped-def]
    assert (
        _create(db_session, UserFactory(), patient_reference=reference).patient_reference
        == reference
    )


def test_a_nothing_is_stored_when_validation_fails(db_session) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory()
    with pytest.raises(ValidationFailed):
        _create(db_session, user, alias="x")
    assert db_session.execute(select(Case)).first() is None


# --- Editing -----------------------------------------------------------------------------------


def test_edits_change_only_the_named_fields_and_are_audited_by_name(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(alias="Old Alias", region="Nairobi")
    case_service.update_draft(
        db_session, case, case.created_by, {"alias": "New Alias", "story": "Thank you all."}
    )
    assert (case.alias, case.region, case.story) == ("New Alias", "Nairobi", "Thank you all.")
    [row] = db_session.execute(select(AuditLog)).scalars()
    assert row.action == "case.updated"
    assert row.details == {"fields": ["alias", "story"]}
    assert "New Alias" not in str(row.details)
    assert "Thank you" not in str(row.details)


def test_an_empty_edit_changes_and_records_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    case_service.update_draft(db_session, case, case.created_by, {})
    assert db_session.execute(select(AuditLog)).first() is None


def test_changing_the_patient_reference_updates_the_hash(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    before = case.patient_ref_hmac
    case_service.update_draft(db_session, case, case.created_by, {"patient_reference": "IP-77"})
    assert case.patient_reference == "IP-77"
    assert case.patient_ref_hmac != before
    assert case.patient_ref_hmac == crypto.lookup_hash("IP-77", context="patient_ref")


@pytest.mark.parametrize(
    "status",
    [
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.CANCELLED,
    ],
)
def test_only_drafts_can_be_edited(db_session, status: CaseStatus) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=status)
    with pytest.raises(Conflict) as raised:
        case_service.update_draft(db_session, case, case.created_by, {"alias": "Changed"})
    assert raised.value.code == "case_not_editable"
    assert case.alias != "Changed"


def test_the_story_may_not_name_an_illness(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    with pytest.raises(ValidationFailed) as raised:
        case_service.update_draft(
            db_session, case, case.created_by, {"story": "Mama has breast cancer and needs surgery"}
        )
    assert raised.value.code == "story_medical_terms"
    assert raised.value.details["terms"] == ["breast cancer"]
    assert "story" in raised.value.details["json"]
    assert case.story is None


@pytest.mark.parametrize(
    "story",
    ["Reach me on 0712345678", "write to mama@example.test", "account 123456789"],
)
def test_the_story_may_not_carry_contact_details(db_session, story: str) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    with pytest.raises(ValidationFailed) as raised:
        case_service.update_draft(db_session, case, case.created_by, {"story": story})
    assert raised.value.code == "story_personal_data"


def test_the_story_has_a_length_limit_and_can_be_cleared(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(story="Something kind.")
    with pytest.raises(ValidationFailed) as raised:
        case_service.update_draft(db_session, case, case.created_by, {"story": "x" * 601})
    assert raised.value.code == "story_length"
    for empty in ("", "   ", None):
        case.story = "Something kind."
        case_service.update_draft(db_session, case, case.created_by, {"story": empty})
        assert case.story is None
    case_service.update_draft(db_session, case, case.created_by, {"story": "x" * 600})
    assert len(case.story) == 600


def test_marking_a_case_as_a_childs_needs_guardian_consents(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE, actor=ConsentActor.SELF)
    with pytest.raises(Conflict) as raised:
        case_service.update_draft(db_session, case, case.created_by, {"is_minor": True})
    assert raised.value.code == "guardian_consent_required"
    assert case.is_minor is False


def test_a_withdrawn_self_consent_does_not_block_it(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    ConsentFactory(
        case=case,
        scope=ConsentScope.PUBLIC_PAGE,
        actor=ConsentActor.SELF,
        withdrawn_at=datetime.now(UTC),
    )
    ConsentFactory(case=case, scope=ConsentScope.RECEIPT_SHARING, actor=ConsentActor.GUARDIAN)
    case_service.update_draft(db_session, case, case.created_by, {"is_minor": True})
    assert case.is_minor is True


def test_a_childs_photo_is_never_shown(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(is_minor=True)
    with pytest.raises(ValidationFailed) as raised:
        case_service.update_draft(db_session, case, case.created_by, {"show_photo": True})
    assert raised.value.code == "photo_not_allowed_for_minor"
    both = CaseFactory()
    with pytest.raises(ValidationFailed):
        case_service.update_draft(
            db_session, both, both.created_by, {"is_minor": True, "show_photo": True}
        )


def test_marking_a_case_as_a_childs_hides_an_existing_photo_choice(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(show_photo=True)
    case_service.update_draft(db_session, case, case.created_by, {"is_minor": True})
    assert case.show_photo is False


def test_an_adult_may_choose_to_show_a_photo(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    case_service.update_draft(db_session, case, case.created_by, {"show_photo": True})
    assert case.show_photo is True


# --- Deleting ----------------------------------------------------------------------------------


def test_deleting_a_draft_cancels_it(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    case_service.delete_draft(case, case.created_by)
    assert case.status == CaseStatus.CANCELLED
    row = db_session.execute(
        select(AuditLog).where(AuditLog.action == "case.transitioned")
    ).scalar_one()
    assert row.details["reason"] == "draft_deleted"


@pytest.mark.parametrize(
    "status", [CaseStatus.PENDING_TRIAGE, CaseStatus.LIVE, CaseStatus.CANCELLED]
)
def test_only_drafts_can_be_deleted(db_session, status: CaseStatus) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=status)
    with pytest.raises(Conflict) as raised:
        case_service.delete_draft(case, case.created_by)
    assert raised.value.code == "case_not_draft"
    assert case.status == status


# --- Money -------------------------------------------------------------------------------------


def test_recompute_totals_sums_what_is_counted_and_nothing_else(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    rate = FxRateFactory(rate=Decimal("100.00000000"))
    DonationFactory(case=case, status=DonationStatus.CAPTURED, net_usd_minor=2000, fx_rate=rate)
    DonationFactory(
        case=case,
        status=DonationStatus.PARTIALLY_REFUNDED,
        net_usd_minor=1000,
        refunded_usd_minor=400,
        fx_rate=rate,
    )
    DonationFactory(case=case, status=DonationStatus.CREATED, net_usd_minor=9999, fx_rate=rate)
    DonationFactory(case=case, status=DonationStatus.DENIED, net_usd_minor=7777, fx_rate=rate)
    db_session.flush()
    db_session.refresh(case)

    case_service.recompute_totals(case)

    assert case.captured_net_usd_minor == 3000
    assert case.refunded_usd_minor == 400
    assert case.pledged_kes_minor == (2000 + 600) * 100
    assert case.donor_count == 2
    assert case.settled_usd_minor == case.settled_kes_minor == 0


def test_only_succeeded_settlements_count_as_settled(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    SettlementFactory(
        case=case,
        tranche_no=1,
        status=SettlementStatus.SUCCEEDED,
        amount_usd_minor=20_000,
        amount_kes_minor=2_580_000,
    )
    SettlementFactory(
        case=case,
        tranche_no=2,
        status=SettlementStatus.FAILED,
        amount_usd_minor=5_000,
        amount_kes_minor=645_000,
    )
    db_session.flush()
    db_session.refresh(case)
    case_service.recompute_totals(case)
    assert (case.settled_usd_minor, case.settled_kes_minor) == (20_000, 2_580_000)


def test_recompute_is_stable_when_run_twice(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    DonationFactory(
        case=case, status=DonationStatus.CAPTURED, net_usd_minor=2500, fx_rate=FxRateFactory()
    )
    db_session.flush()
    db_session.refresh(case)
    case_service.recompute_totals(case)
    first = (case.pledged_kes_minor, case.donor_count)
    case_service.recompute_totals(case)
    assert (case.pledged_kes_minor, case.donor_count) == first


@pytest.mark.parametrize(
    ("status", "target", "pledged", "accepting"),
    [
        (CaseStatus.LIVE, 10_000_000, 0, True),
        (CaseStatus.LIVE, 10_000_000, 10_499_999, True),
        (CaseStatus.LIVE, 10_000_000, 10_500_000, False),  # target plus the 5 percent buffer
        (CaseStatus.LIVE, 10_000_000, 12_000_000, False),
        (CaseStatus.LIVE, None, 0, False),  # no confirmed target yet
        (CaseStatus.FUNDED, 10_000_000, 0, False),
        (CaseStatus.PENDING_CONFIRMATION, 10_000_000, 0, False),
        (CaseStatus.CLOSED, 10_000_000, 0, False),
        (CaseStatus.FROZEN, 10_000_000, 0, False),
    ],
)
def test_a_case_accepts_donations_only_while_live_and_below_goal(
    db_session, status, target, pledged, accepting
) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(
        status=status,
        target_kes_minor=target,
        pledged_kes_minor=pledged,
        close_reason="fully_settled" if status == CaseStatus.CLOSED else None,
        freeze_reason="x" if status == CaseStatus.FROZEN else None,
    )
    assert case_service.is_accepting_donations(case) is accepting


def test_the_goal_is_the_target_plus_its_buffer(db_session) -> None:  # type: ignore[no-untyped-def]
    assert (
        case_service.goal_kes_minor(CaseFactory(target_kes_minor=10_000_000, buffer_bps=500))
        == 10_500_000
    )
    assert case_service.goal_kes_minor(CaseFactory(target_kes_minor=None)) is None


# --- Timeline ----------------------------------------------------------------------------------


def test_the_timeline_lists_each_status_in_order(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory()
    case_service.transition(case, CaseStatus.PENDING_TRIAGE, case.created_by)
    case_service.transition(case, CaseStatus.IN_REVIEW, case.created_by, "flagged")
    case_service.transition(case, CaseStatus.CANCELLED, case.created_by)
    timeline = case_service.status_timeline(db_session, case)
    assert [entry["status"] for entry in timeline] == [
        "draft",
        "pending_triage",
        "in_review",
        "cancelled",
    ]
    stamps = [entry["at"] for entry in timeline]
    assert stamps == sorted(stamps)
    assert stamps[-1] - stamps[0] < timedelta(minutes=1)


def test_another_cases_history_does_not_appear(db_session) -> None:  # type: ignore[no-untyped-def]
    mine, other = CaseFactory(), CaseFactory()
    case_service.transition(other, CaseStatus.PENDING_TRIAGE, other.created_by)
    assert [e["status"] for e in case_service.status_timeline(db_session, mine)] == ["draft"]
