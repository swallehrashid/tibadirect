import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.enums import SettlementMethod, StaffRole, UserRole
from app.models.hospital import Hospital, HospitalStaff, normalize_facility_name
from tests.factories import (
    FacilityRegisterEntryFactory,
    HospitalFactory,
    HospitalStaffFactory,
)
from tests.models.helpers import assert_db_rejects


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("Kilele Mission Hospital", "kilele mission hospital"),
        ("St. Mary's  Hospital (Level 4)", "st mary s hospital level 4"),
        ("MTO-MWEMA   HOSPITAL", "mto mwema hospital"),
    ],
)
def test_normalize_facility_name(name: str, normalized: str) -> None:
    assert normalize_facility_name(name) == normalized


def test_register_code_is_unique(db_session) -> None:
    FacilityRegisterEntryFactory(register_code="REG-1")
    error = assert_db_rejects(
        db_session, lambda: FacilityRegisterEntryFactory(register_code="REG-1")
    )
    assert "uq_facility_register_entries_register_code" in str(error)


def test_register_entry_requires_an_official_email(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: FacilityRegisterEntryFactory(official_email=None))
    assert isinstance(error, IntegrityError)


def test_register_entry_name_is_indexed_for_matching(db_session) -> None:
    names = set(
        db_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'facility_register_entries'")
        ).scalars()
    )
    assert "ix_facility_register_entries_normalized_name" in names


def test_hospital_defaults(db_session) -> None:
    hospital = HospitalFactory()
    db_session.refresh(hospital)
    assert hospital.is_active is True
    assert hospital.settlement_method is SettlementMethod.PAYPAL_PAYOUTS
    assert hospital.two_person_threshold_kes_minor == 20_000_000  # KES 200,000
    assert hospital.paypal_payout_email is None
    assert hospital.settlement_account_ref_enc is None


def test_hospital_and_register_entry_link_both_ways(db_session) -> None:
    hospital = HospitalFactory()
    db_session.flush()
    assert hospital.register_entry.hospital is hospital
    assert hospital.display_name == hospital.register_entry.name


def test_official_email_comes_from_the_register_only(db_session) -> None:
    entry = FacilityRegisterEntryFactory(official_email="billing@kilele-hospital.test")
    hospital = HospitalFactory(register_entry=entry, display_name="Kilele Mission Hospital")
    assert hospital.official_email == "billing@kilele-hospital.test"


def test_one_hospital_per_register_entry(db_session) -> None:
    entry = FacilityRegisterEntryFactory()
    HospitalFactory(register_entry=entry)
    db_session.flush()
    # Set the foreign key directly: assigning the relationship would move the one-to-one link.
    duplicate = Hospital(register_entry_id=entry.id, display_name="Duplicate", county="Nairobi")
    error = assert_db_rejects(db_session, lambda: db_session.add(duplicate))
    assert "uq_hospitals_register_entry_id" in str(error)


def test_negative_two_person_threshold_is_rejected(db_session) -> None:
    error = assert_db_rejects(
        db_session, lambda: HospitalFactory(two_person_threshold_kes_minor=-1)
    )
    assert "ck_hospitals_two_person_threshold_non_negative" in str(error)


def test_zero_threshold_means_every_bill_needs_two_people(db_session) -> None:
    assert HospitalFactory(two_person_threshold_kes_minor=0).two_person_threshold_kes_minor == 0


def test_a_register_entry_in_use_cannot_be_deleted(db_session) -> None:
    hospital = HospitalFactory()
    db_session.flush()
    entry = hospital.register_entry
    error = assert_db_rejects(db_session, lambda: db_session.delete(entry))
    assert isinstance(error, IntegrityError)


def test_settlement_account_reference_is_encrypted_at_rest(db_session) -> None:
    hospital = HospitalFactory(settlement_account_ref_enc=b"PAYBILL 400200 / ACC 998877")
    db_session.flush()
    stored = bytes(
        db_session.execute(text("SELECT settlement_account_ref_enc FROM hospitals")).scalar_one()
    )
    assert b"400200" not in stored
    db_session.expire(hospital)
    assert hospital.settlement_account_ref_enc == b"PAYBILL 400200 / ACC 998877"


def test_hospital_repr_shows_only_the_id(db_session) -> None:
    hospital = HospitalFactory(
        paypal_payout_email="payouts@kilele.test", settlement_account_ref_enc=b"secret-ref"
    )
    text_repr = repr(hospital)
    assert "secret-ref" not in text_repr
    assert "payouts@" not in text_repr
    assert str(hospital.id) in text_repr


def test_staff_links_user_and_hospital_both_ways(db_session) -> None:
    staff = HospitalStaffFactory(staff_role=StaffRole.BILLING_SUPERVISOR)
    db_session.flush()
    assert staff.user.staff_profile is staff
    assert staff in staff.hospital.staff
    assert staff.user.role is UserRole.HOSPITAL_STAFF
    assert staff.is_active is True


def test_staff_role_is_a_lowercase_native_enum(db_session) -> None:
    HospitalStaffFactory(staff_role=StaffRole.BILLING_SUPERVISOR)
    stored = db_session.execute(text("SELECT staff_role::text FROM hospital_staff")).scalar_one()
    assert stored == "billing_supervisor"


def test_a_user_belongs_to_at_most_one_hospital(db_session) -> None:
    staff = HospitalStaffFactory()
    other_hospital = HospitalFactory()
    db_session.flush()
    second = HospitalStaff(
        user_id=staff.user.id, hospital_id=other_hospital.id, staff_role=StaffRole.BILLING_CLERK
    )
    error = assert_db_rejects(db_session, lambda: db_session.add(second))
    assert "uq_hospital_staff_user_id" in str(error)


def test_a_hospital_can_have_several_staff(db_session) -> None:
    hospital = HospitalFactory()
    HospitalStaffFactory(hospital=hospital, staff_role=StaffRole.BILLING_CLERK)
    HospitalStaffFactory(hospital=hospital, staff_role=StaffRole.BILLING_SUPERVISOR)
    db_session.flush()
    assert {s.staff_role for s in hospital.staff} == {
        StaffRole.BILLING_CLERK,
        StaffRole.BILLING_SUPERVISOR,
    }


def test_a_hospital_with_staff_cannot_be_deleted(db_session) -> None:
    staff = HospitalStaffFactory()
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: db_session.delete(staff.hospital))
    assert isinstance(error, IntegrityError)


def test_a_user_with_a_staff_profile_cannot_be_deleted(db_session) -> None:
    staff = HospitalStaffFactory()
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: db_session.delete(staff.user))
    assert isinstance(error, IntegrityError)


def test_staff_must_have_a_role(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: HospitalStaffFactory(staff_role=None))
    assert isinstance(error, IntegrityError)
