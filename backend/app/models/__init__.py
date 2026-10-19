"""SQLAlchemy models. Every model module is imported here so Alembic sees the full metadata."""

from app.models.case import Case, CaseDocument, Consent, ReviewItem, TriageResult
from app.models.hospital import FacilityRegisterEntry, Hospital, HospitalStaff
from app.models.identity import LoginCode, User
from app.models.integration import WebhookEvent
from app.models.ledger import LedgerEntry
from app.models.money import Donation, FxRate, Refund, Settlement, SettlementAllocation
from app.models.verification import Confirmation, ConfirmationRequest

__all__ = [
    "Case",
    "CaseDocument",
    "Confirmation",
    "ConfirmationRequest",
    "Consent",
    "Donation",
    "FacilityRegisterEntry",
    "FxRate",
    "Hospital",
    "HospitalStaff",
    "LedgerEntry",
    "LoginCode",
    "Refund",
    "ReviewItem",
    "Settlement",
    "SettlementAllocation",
    "TriageResult",
    "User",
    "WebhookEvent",
]
