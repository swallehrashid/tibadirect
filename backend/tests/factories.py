"""factory_boy factories. They flush into the test transaction's session and never commit."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import factory
from factory.alchemy import SQLAlchemyModelFactory

from app.extensions import db
from app.models.case import Case, CaseDocument, Consent, ReviewItem, TriageResult, hash_patient_ref
from app.models.enums import (
    ActorType,
    AgentActionType,
    AppealInputKind,
    ConfirmationStatus,
    ConsentActor,
    ConsentScope,
    DocumentKind,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    ReviewSource,
    RiskBand,
    SettlementMethod,
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
from app.models.integration import WebhookEvent
from app.models.ledger import LedgerEntry
from app.models.money import Donation, FxRate, Refund, Settlement
from app.models.platform import AgentAction, AppealCheck, AuditLog
from app.models.verification import Confirmation, ConfirmationRequest


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


class ConfirmationRequestFactory(BaseFactory):
    class Meta:
        model = ConfirmationRequest

    case = factory.SubFactory(CaseFactory)
    hospital_id = factory.LazyAttribute(lambda o: o.case.hospital_id)
    requested_by = factory.LazyAttribute(lambda o: HospitalStaffFactory(hospital=o.case.hospital))
    purpose = "decision"
    destination_masked = "bil***@hospital.test"
    otp_hmac = factory.Sequence(lambda n: f"{n:064x}")
    expires_at = factory.LazyFunction(lambda: datetime.now(UTC) + timedelta(minutes=10))


class ConfirmationFactory(BaseFactory):
    class Meta:
        model = Confirmation

    case = factory.SubFactory(CaseFactory)
    hospital_id = factory.LazyAttribute(lambda o: o.case.hospital_id)
    document = factory.LazyAttribute(lambda o: CaseDocumentFactory(case=o.case))
    confirmation_request = factory.LazyAttribute(lambda o: ConfirmationRequestFactory(case=o.case))
    confirmed_by = factory.LazyAttribute(lambda o: o.confirmation_request.requested_by)
    status = ConfirmationStatus.CONFIRMED
    amount_kes_minor = 8_500_000
    invoice_number = factory.Sequence(lambda n: f"INV-{n:05d}")
    finalized_at = factory.LazyFunction(lambda: datetime.now(UTC))


class FxRateFactory(BaseFactory):
    class Meta:
        model = FxRate

    base = "USD"
    quote = "KES"
    rate = Decimal("129.00000000")
    source = "test"
    fetched_at = factory.LazyFunction(lambda: datetime.now(UTC))


class DonationFactory(BaseFactory):
    class Meta:
        model = Donation

    case = factory.SubFactory(CaseFactory)
    paypal_order_id = factory.Sequence(lambda n: f"ORDER-{n:06d}")
    amount_usd_minor = 2500


class SettlementFactory(BaseFactory):
    class Meta:
        model = Settlement

    case = factory.SubFactory(CaseFactory)
    confirmation = factory.LazyAttribute(lambda o: ConfirmationFactory(case=o.case))
    tranche_no = 1
    adapter = SettlementMethod.PAYPAL_PAYOUTS
    amount_usd_minor = 20_000
    amount_kes_minor = 2_580_000
    fx_rate = factory.SubFactory(FxRateFactory)
    idempotency_key = factory.Sequence(lambda n: f"settle-{n:06d}")


class RefundFactory(BaseFactory):
    class Meta:
        model = Refund

    donation = factory.SubFactory(DonationFactory, net_usd_minor=2500)
    amount_usd_minor = 500
    reason = RefundReason.EXCESS_POLICY
    idempotency_key = factory.Sequence(lambda n: f"refund-{n:06d}")
    requested_by = ActorType.SYSTEM


class LedgerEntryFactory(BaseFactory):
    class Meta:
        model = LedgerEntry

    case = factory.SubFactory(CaseFactory)
    entry_type = LedgerEntryType.DONATION_CAPTURED
    account = LedgerAccount.CASE_FUNDS
    amount_minor = 2500
    currency = "USD"
    ref_type = "donation"
    ref_id = factory.LazyFunction(uuid.uuid4)
    prev_hash = factory.Sequence(lambda n: f"{n:064x}")
    hash = factory.Sequence(lambda n: f"{n + 1_000_000:064x}")


class WebhookEventFactory(BaseFactory):
    class Meta:
        model = WebhookEvent

    paypal_event_id = factory.Sequence(lambda n: f"WH-{n:06d}")
    event_type = "PAYMENT.CAPTURE.COMPLETED"
    payload = factory.LazyFunction(lambda: {"id": "WH-1", "resource": {"id": "CAP-1"}})
    signature_verified = True


class AppealCheckFactory(BaseFactory):
    class Meta:
        model = AppealCheck

    input_hmac = factory.Sequence(lambda n: f"{n:064x}")
    input_kind = AppealInputKind.TEXT
    risk_score = 80
    risk_band = RiskBand.HIGH
    reasons_en = factory.LazyFunction(lambda: ["It asks you to send money to a personal number."])
    reasons_sw = factory.LazyFunction(lambda: ["Inakuomba utume pesa kwa nambari ya mtu binafsi."])
    used_llm = False


class AuditLogFactory(BaseFactory):
    class Meta:
        model = AuditLog

    actor_type = ActorType.SYSTEM
    action = "case.submitted"
    target_type = "case"
    target_id = factory.LazyFunction(uuid.uuid4)


class AgentActionFactory(BaseFactory):
    class Meta:
        model = AgentAction

    action_type = AgentActionType.REFUND
    case = factory.SubFactory(CaseFactory)
    payload = factory.LazyFunction(
        lambda: {"donation_id": str(uuid.uuid4()), "amount_usd_minor": 500}
    )
    rationale = "The donation exceeds what the hospital needs."
