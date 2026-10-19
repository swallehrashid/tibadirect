"""factory_boy factories. They flush into the test transaction's session and never commit."""

from datetime import UTC, datetime, timedelta

import factory
from factory.alchemy import SQLAlchemyModelFactory

from app.extensions import db
from app.models.enums import StaffRole, UserRole
from app.models.hospital import (
    FacilityRegisterEntry,
    Hospital,
    HospitalStaff,
    normalize_facility_name,
)
from app.models.identity import LoginCode, User


class BaseFactory(SQLAlchemyModelFactory):
    class Meta:
        abstract = True
        sqlalchemy_session_factory = lambda: db.session  # noqa: E731
        sqlalchemy_session_persistence = "flush"


class UserFactory(BaseFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.test")
    display_name = factory.Sequence(lambda n: f"Test User {n}")
    role = UserRole.FAMILY


class LoginCodeFactory(BaseFactory):
    class Meta:
        model = LoginCode

    email = factory.Sequence(lambda n: f"user{n}@example.test")
    code_hmac = factory.Sequence(lambda n: f"{n:064x}")
    expires_at = factory.LazyFunction(lambda: datetime.now(UTC) + timedelta(minutes=10))


class FacilityRegisterEntryFactory(BaseFactory):
    class Meta:
        model = FacilityRegisterEntry

    register_code = factory.Sequence(lambda n: f"REG-{n:05d}")
    name = factory.Sequence(lambda n: f"Test Mission Hospital {n}")
    normalized_name = factory.LazyAttribute(lambda o: normalize_facility_name(o.name))
    county = "Nairobi"
    official_email = factory.Sequence(lambda n: f"billing@hospital{n}.test")
    source = "test"


class HospitalFactory(BaseFactory):
    class Meta:
        model = Hospital

    register_entry = factory.SubFactory(FacilityRegisterEntryFactory)
    display_name = factory.SelfAttribute("register_entry.name")
    county = factory.SelfAttribute("register_entry.county")


class HospitalStaffFactory(BaseFactory):
    class Meta:
        model = HospitalStaff

    user = factory.SubFactory(UserFactory, role=UserRole.HOSPITAL_STAFF)
    hospital = factory.SubFactory(HospitalFactory)
    staff_role = StaffRole.BILLING_CLERK
