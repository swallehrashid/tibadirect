"""Enumerations stored as native PostgreSQL enums (lowercase values).

The initial migration writes the same values out literally; a test compares the database's enum
labels with this module, so the two cannot drift apart.
"""

import re
from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def db_enum_name(enum_cls: type[StrEnum]) -> str:
    """The PostgreSQL type name for an enum class: ``CaseStatus`` becomes ``case_status``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", enum_cls.__name__).lower()


def pg_enum(enum_cls: type[StrEnum], name: str | None = None) -> SAEnum:
    """Map a ``StrEnum`` to a native PostgreSQL enum that stores the lowercase values."""
    return SAEnum(
        enum_cls,
        name=name or db_enum_name(enum_cls),
        values_callable=lambda e: [member.value for member in e],
        native_enum=True,
        validate_strings=True,
    )


class UserRole(StrEnum):
    FAMILY = "family"
    HOSPITAL_STAFF = "hospital_staff"
    ADMIN = "admin"


class StaffRole(StrEnum):
    BILLING_CLERK = "billing_clerk"
    BILLING_SUPERVISOR = "billing_supervisor"


class SettlementMethod(StrEnum):
    PAYPAL_PAYOUTS = "paypal_payouts"
    MOCK_PARTNER = "mock_partner"
    JENGA = "jenga"


class CaseStatus(StrEnum):
    DRAFT = "draft"
    PENDING_TRIAGE = "pending_triage"
    IN_REVIEW = "in_review"
    PENDING_CONFIRMATION = "pending_confirmation"
    LIVE = "live"
    FUNDED = "funded"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    FROZEN = "frozen"


class TrustLevel(StrEnum):
    UNVERIFIED = "unverified"
    PENDING_CONFIRMATION = "pending_confirmation"
    HOSPITAL_CONFIRMED = "hospital_confirmed"


class CloseReason(StrEnum):
    FULLY_SETTLED = "fully_settled"
    FINAL_BILL_LOWER = "final_bill_lower"
    TREATMENT_CANCELLED = "treatment_cancelled"
    PATIENT_DECEASED = "patient_deceased"
    ADMIN_CANCELLED = "admin_cancelled"
    FRAUD = "fraud"


class DocumentKind(StrEnum):
    INVOICE = "invoice"
    ESTIMATE = "estimate"
    FINAL_BILL = "final_bill"
    PHOTO = "photo"


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    TRIAGED = "triaged"
    FAILED = "failed"


class RiskBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TriageRoute(StrEnum):
    HOSPITAL_CONFIRMATION = "hospital_confirmation"
    MANUAL_REVIEW = "manual_review"


class ConsentScope(StrEnum):
    HOSPITAL_VERIFICATION = "hospital_verification"
    PUBLIC_PAGE = "public_page"
    AI_PROCESSING_ABROAD = "ai_processing_abroad"
    RECEIPT_SHARING = "receipt_sharing"


class ConsentActor(StrEnum):
    SELF = "self"
    GUARDIAN = "guardian"
    ADMINISTRATOR = "administrator"


class OtpChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class ConfirmationRequestStatus(StrEnum):
    SENT = "sent"
    VERIFIED = "verified"
    EXPIRED = "expired"
    LOCKED = "locked"


class ConfirmationStatus(StrEnum):
    AWAITING_SECOND_APPROVAL = "awaiting_second_approval"
    CONFIRMED = "confirmed"
    QUERIED = "queried"
    REJECTED = "rejected"


class ExcessChoice(StrEnum):
    REFUND = "refund"
    SOLIDARITY_POOL = "solidarity_pool"
    FOLLOW_UP_CARE = "follow_up_care"


class DonationStatus(StrEnum):
    CREATED = "created"
    CAPTURED = "captured"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    REVERSED = "reversed"
    DENIED = "denied"
    NOT_CAPTURED_CASE_CLOSED = "not_captured_case_closed"


class SettlementStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RefundReason(StrEnum):
    EXCESS_POLICY = "excess_policy"
    FREEZE = "freeze"
    CANCELLATION = "cancellation"
    ADMIN = "admin"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LedgerEntryType(StrEnum):
    DONATION_CAPTURED = "donation_captured"
    PAYPAL_FEE = "paypal_fee"
    SETTLEMENT_SENT = "settlement_sent"
    REFUND_ISSUED = "refund_issued"
    POOL_TRANSFER = "pool_transfer"
    REVERSAL = "reversal"
    ADJUSTMENT = "adjustment"


class LedgerAccount(StrEnum):
    CASE_FUNDS = "case_funds"
    PAYPAL_FEES = "paypal_fees"
    HOSPITAL_SETTLED = "hospital_settled"
    DONOR_REFUNDS = "donor_refunds"
    SOLIDARITY_POOL = "solidarity_pool"


class WebhookStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


class ReviewSource(StrEnum):
    AI_FLAG = "ai_flag"
    FAMILY_REQUEST = "family_request"
    INSIDER_ANOMALY = "insider_anomaly"
    ADMIN = "admin"


class ReviewStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class ReviewResolution(StrEnum):
    APPROVED_FOR_CONFIRMATION = "approved_for_confirmation"
    KEPT_UNVERIFIED = "kept_unverified"
    CASE_CANCELLED = "case_cancelled"
    NO_ACTION = "no_action"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
    PAYPAL = "paypal"


class AgentActionType(StrEnum):
    REFUND = "refund"
    PAYOUT = "payout"
    FREEZE = "freeze"


class AgentActionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class AppealInputKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"


ALL_ENUMS: tuple[type[StrEnum], ...] = (
    UserRole,
    StaffRole,
    SettlementMethod,
    CaseStatus,
    TrustLevel,
    CloseReason,
    DocumentKind,
    DocumentStatus,
    RiskBand,
    TriageRoute,
    ConsentScope,
    ConsentActor,
    OtpChannel,
    ConfirmationRequestStatus,
    ConfirmationStatus,
    ExcessChoice,
    DonationStatus,
    SettlementStatus,
    RefundReason,
    RefundStatus,
    LedgerEntryType,
    LedgerAccount,
    WebhookStatus,
    ReviewSource,
    ReviewStatus,
    ReviewResolution,
    ActorType,
    AgentActionType,
    AgentActionStatus,
    AppealInputKind,
)
