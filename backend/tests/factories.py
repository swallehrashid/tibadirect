"""factory_boy factories. They flush into the test transaction's session and never commit."""

from datetime import UTC, datetime, timedelta

import factory
from factory.alchemy import SQLAlchemyModelFactory

from app.extensions import db
from app.models.case import Case, CaseDocument, Consent, ReviewItem, TriageResult, hash_patient_ref
from app.models.enums import (
    ConsentActor,
    ConsentScope,
    DocumentKind,
    ReviewSource,
    RiskBand,
    StaffRole,
    TriageRoute,
    UserRole,
)
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


class CaseFactory(BaseFactory):
    class Meta:
        model = Case

    created_by = factory.SubFactory(UserFactory)
    hospital = factory.SubFactory(HospitalFactory)
    alias = factory.Sequence(lambda n: f"Family {n}")
    region = "Nairobi"
    patient_ref_enc = factory.Sequence(lambda n: f"ADM-{n:06d}".encode())
    patient_ref_hmac = factory.LazyAttribute(lambda o: hash_patient_ref(o.patient_ref_enc.decode()))


class CaseDocumentFactory(BaseFactory):
    class Meta:
        model = CaseDocument

    case = factory.SubFactory(CaseFactory)
    uploaded_by_id = factory.LazyAttribute(lambda o: o.case.created_by_id)
    kind = DocumentKind.INVOICE
    mime_type = "application/pdf"
    size_bytes = 120_000
    sha256 = factory.Sequence(lambda n: f"{n:064x}")


class TriageResultFactory(BaseFactory):
    class Meta:
        model = TriageResult

    document = factory.SubFactory(CaseDocumentFactory)
    case = factory.SelfAttribute("document.case")
    extracted = factory.LazyFunction(lambda: {"total_kes": "85000.00", "currency": "KES"})
    signals = factory.LazyFunction(list)
    risk_score = 10
    risk_band = RiskBand.LOW
    route = TriageRoute.HOSPITAL_CONFIRMATION
    reasons_en = factory.LazyFunction(lambda: ["Automatic checks pending"])
    reasons_sw = factory.LazyFunction(lambda: ["Ukaguzi wa kiotomatiki unasubiri"])
    used_llm = False
    model_versions = factory.LazyFunction(lambda: {"extractor": "local-v1"})
    duration_ms = 1200


class ConsentFactory(BaseFactory):
    class Meta:
        model = Consent

    case = factory.SubFactory(CaseFactory)
    scope = ConsentScope.PUBLIC_PAGE
    actor = ConsentActor.SELF
    granted_by_id = factory.LazyAttribute(lambda o: o.case.created_by_id)
    wording_version = "v1"
    language = "en"


class ReviewItemFactory(BaseFactory):
    class Meta:
        model = ReviewItem

    case = factory.SubFactory(CaseFactory)
    source = ReviewSource.AI_FLAG
    reason = "Duplicate invoice flagged"
