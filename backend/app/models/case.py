"""Cases, their bills, triage results, consents and human review items."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    CaseStatus,
    CloseReason,
    ConsentActor,
    ConsentScope,
    DocumentKind,
    DocumentStatus,
    ReviewResolution,
    ReviewSource,
    ReviewStatus,
    RiskBand,
    TriageRoute,
    TrustLevel,
    pg_enum,
)
from app.models.types import EncryptedBytes
from app.security import crypto

if TYPE_CHECKING:
    from app.models.hospital import Hospital
    from app.models.identity import User
    from app.models.ledger import LedgerEntry
    from app.models.money import Donation, Settlement
    from app.models.platform import AgentAction, AppealCheck
    from app.models.verification import Confirmation, ConfirmationRequest

# Statuses in which a case page may be public (together with an active public_page consent).
PUBLIC_STATUSES = frozenset({CaseStatus.LIVE, CaseStatus.FUNDED, CaseStatus.CLOSED})

# Separates patient-reference hashes from every other use of the pepper.
PATIENT_REF_CONTEXT = "patient_ref"

MAX_UPLOAD_BYTES = 10_485_760

_EMPTY_JSON = text("'{}'::jsonb")

# Python None must become SQL NULL, not the JSON value null,
# so the NOT NULL constraint actually protects the column.
JSONB_NOT_NULL = JSONB(none_as_null=True)


def hash_patient_ref(reference: str) -> str:
    """Keyed hash used to detect the same patient in two active cases."""
    return crypto.lookup_hash(reference, context=PATIENT_REF_CONTEXT)


class Case(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """A fundraising case for one hospital bill. There is deliberately no diagnosis column."""

    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("target_kes_minor >= 0", name="target_non_negative"),
        CheckConstraint("buffer_bps BETWEEN 0 AND 2000", name="buffer_range"),
        CheckConstraint("usd_goal_minor >= 0", name="usd_goal_non_negative"),
        CheckConstraint("pledged_kes_minor >= 0", name="pledged_non_negative"),
        CheckConstraint("settled_kes_minor >= 0", name="settled_non_negative"),
        CheckConstraint("captured_net_usd_minor >= 0", name="captured_net_non_negative"),
        CheckConstraint("settled_usd_minor >= 0", name="settled_usd_non_negative"),
        CheckConstraint("refunded_usd_minor >= 0", name="refunded_non_negative"),
        CheckConstraint("status <> 'closed' OR close_reason IS NOT NULL", name="closed_has_reason"),
        CheckConstraint(
            "status <> 'frozen' OR freeze_reason IS NOT NULL", name="frozen_has_reason"
        ),
        Index("ix_cases_hospital_status", "hospital_id", "status"),
        Index(
            "ix_cases_active_patient_ref",
            "patient_ref_hmac",
            postgresql_where=text("status NOT IN ('closed','cancelled')"),
        ),
    )

    public_slug: Mapped[str] = mapped_column(
        String(12), unique=True, default=crypto.new_public_slug
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="RESTRICT"), index=True
    )
    alias: Mapped[str] = mapped_column(String(40))
    region: Mapped[str] = mapped_column(String(40))
    story: Mapped[str | None] = mapped_column(String(600))
    patient_ref_enc: Mapped[bytes] = mapped_column(EncryptedBytes)
    patient_ref_hmac: Mapped[str] = mapped_column(CHAR(64))
    is_minor: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    show_photo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # P1: the relationship is added with the photo feature. use_alter breaks the table cycle.
    photo_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("case_documents.id", ondelete="SET NULL", use_alter=True)
    )
    status: Mapped[CaseStatus] = mapped_column(
        pg_enum(CaseStatus),
        default=CaseStatus.DRAFT,
        server_default=CaseStatus.DRAFT.value,
        index=True,
    )
    trust_level: Mapped[TrustLevel] = mapped_column(
        pg_enum(TrustLevel),
        default=TrustLevel.UNVERIFIED,
        server_default=TrustLevel.UNVERIFIED.value,
    )
    target_kes_minor: Mapped[int | None] = mapped_column(BigInteger)
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    buffer_bps: Mapped[int] = mapped_column(Integer, default=500, server_default="500")
    usd_goal_minor: Mapped[int | None] = mapped_column(BigInteger)
    pledged_kes_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    settled_kes_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    captured_net_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    settled_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    refunded_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    donor_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    live_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[CloseReason | None] = mapped_column(pg_enum(CloseReason))
    freeze_reason: Mapped[str | None] = mapped_column(String(300))
    follow_up_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    created_by: Mapped["User"] = relationship(back_populates="cases")
    hospital: Mapped["Hospital"] = relationship(back_populates="cases")
    documents: Mapped[list["CaseDocument"]] = relationship(
        back_populates="case",
        foreign_keys="CaseDocument.case_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    consents: Mapped[list["Consent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    triage_results: Mapped[list["TriageResult"]] = relationship(back_populates="case")
    review_items: Mapped[list["ReviewItem"]] = relationship(back_populates="case")
    confirmation_requests: Mapped[list["ConfirmationRequest"]] = relationship(back_populates="case")
    confirmations: Mapped[list["Confirmation"]] = relationship(back_populates="case")
    donations: Mapped[list["Donation"]] = relationship(back_populates="case")
    settlements: Mapped[list["Settlement"]] = relationship(
        back_populates="case", order_by="Settlement.tranche_no"
    )
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="case", order_by="LedgerEntry.seq"
    )
    appeal_checks: Mapped[list["AppealCheck"]] = relationship(back_populates="matched_case")
    agent_actions: Mapped[list["AgentAction"]] = relationship(back_populates="case")

    __mapper_args__ = {"version_id_col": version_id}

    def set_patient_ref(self, reference: str) -> None:
        """Store the hospital's patient or admission reference: encrypted, plus a lookup hash."""
        cleaned = reference.strip()
        if not cleaned:
            raise ValueError("The patient reference cannot be empty")
        self.patient_ref_enc = cleaned.encode()
        self.patient_ref_hmac = hash_patient_ref(cleaned)

    @property
    def patient_reference(self) -> str:
        """The decrypted reference. Only the family owner and the hospital may be shown this."""
        return self.patient_ref_enc.decode()

    def active_consent(self, scope: ConsentScope) -> "Consent | None":
        """The current, not withdrawn consent for ``scope``, if any."""
        for consent in self.consents:
            if consent.scope == scope and consent.withdrawn_at is None:
                return consent
        return None

    @property
    def is_publicly_visible(self) -> bool:
        """A page is public only in a public status and with an active ``public_page`` consent."""
        return (
            self.status in PUBLIC_STATUSES
            and self.active_consent(ConsentScope.PUBLIC_PAGE) is not None
        )

    def __repr__(self) -> str:
        return f"<Case id={self.id} status={self.status}>"


class CaseDocument(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """An uploaded bill or photo. Raw bytes live in private storage; only redacted text is kept."""

    __tablename__ = "case_documents"
    __table_args__ = (
        CheckConstraint(f"size_bytes BETWEEN 1 AND {MAX_UPLOAD_BYTES}", name="size_range"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[DocumentKind] = mapped_column(pg_enum(DocumentKind))
    status: Mapped[DocumentStatus] = mapped_column(
        pg_enum(DocumentStatus),
        default=DocumentStatus.UPLOADED,
        server_default=DocumentStatus.UPLOADED.value,
    )
    storage_key: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(SmallInteger)
    sha256: Mapped[str] = mapped_column(CHAR(64), index=True)
    phash: Mapped[str | None] = mapped_column(CHAR(16), index=True)
    fingerprint: Mapped[str | None] = mapped_column(CHAR(64), index=True)
    redacted_text: Mapped[str | None] = mapped_column(Text)
    raw_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[Case] = relationship(back_populates="documents", foreign_keys=[case_id])
    triage_results: Mapped[list["TriageResult"]] = relationship(back_populates="document")
    confirmations: Mapped[list["Confirmation"]] = relationship(back_populates="document")

    def __repr__(self) -> str:
        return f"<CaseDocument id={self.id} kind={self.kind}>"


class TriageResult(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Output of the AI triage for one document. The newest per document is the current one."""

    __tablename__ = "triage_results"
    __table_args__ = (CheckConstraint("risk_score BETWEEN 0 AND 100", name="risk_score_range"),)

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("case_documents.id", ondelete="RESTRICT"), index=True
    )
    extracted: Mapped[dict[str, Any]] = mapped_column(JSONB_NOT_NULL)
    signals: Mapped[list[Any]] = mapped_column(JSONB_NOT_NULL)
    risk_score: Mapped[int] = mapped_column(SmallInteger)
    risk_band: Mapped[RiskBand] = mapped_column(pg_enum(RiskBand))
    route: Mapped[TriageRoute] = mapped_column(pg_enum(TriageRoute))
    reasons_en: Mapped[list[Any]] = mapped_column(JSONB_NOT_NULL)
    reasons_sw: Mapped[list[Any]] = mapped_column(JSONB_NOT_NULL)
    used_llm: Mapped[bool] = mapped_column(Boolean)
    model_versions: Mapped[dict[str, Any]] = mapped_column(JSONB_NOT_NULL)
    duration_ms: Mapped[int] = mapped_column(Integer)

    case: Mapped[Case] = relationship(back_populates="triage_results")
    document: Mapped[CaseDocument] = relationship(back_populates="triage_results")

    def __repr__(self) -> str:
        return f"<TriageResult id={self.id} band={self.risk_band}>"


class Consent(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A scoped, versioned, withdrawable consent. At most one active consent per case and scope."""

    __tablename__ = "consents"
    __table_args__ = (
        Index(
            "uq_consents_case_scope_active",
            "case_id",
            "scope",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL"),
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[ConsentScope] = mapped_column(pg_enum(ConsentScope))
    actor: Mapped[ConsentActor] = mapped_column(pg_enum(ConsentActor))
    granted_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    wording_version: Mapped[str] = mapped_column(String(20))
    language: Mapped[str] = mapped_column(String(2))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hmac: Mapped[str | None] = mapped_column(CHAR(64))

    case: Mapped[Case] = relationship(back_populates="consents")

    def __repr__(self) -> str:
        return f"<Consent id={self.id} scope={self.scope}>"


class ReviewItem(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """A flag for a human. Resolving one needs a decision and a written reason (s.35)."""

    __tablename__ = "review_items"
    __table_args__ = (
        CheckConstraint(
            "status <> 'resolved' OR (resolution IS NOT NULL AND resolved_by_id IS NOT NULL "
            "AND resolved_at IS NOT NULL AND length(btrim(coalesce(resolution_note, ''))) > 0)",
            name="resolved_has_decision",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    source: Mapped[ReviewSource] = mapped_column(pg_enum(ReviewSource))
    status: Mapped[ReviewStatus] = mapped_column(
        pg_enum(ReviewStatus),
        default=ReviewStatus.OPEN,
        server_default=ReviewStatus.OPEN.value,
        index=True,
    )
    reason: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB_NOT_NULL, default=dict, server_default=_EMPTY_JSON
    )
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    resolution: Mapped[ReviewResolution | None] = mapped_column(pg_enum(ReviewResolution))
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[Case] = relationship(back_populates="review_items")

    def __repr__(self) -> str:
        return f"<ReviewItem id={self.id} status={self.status}>"
