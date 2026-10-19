"""Hospital confirmation: one-time code requests and the confirmations they authorise."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ConfirmationRequestStatus,
    ConfirmationStatus,
    OtpChannel,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.case import Case, CaseDocument
    from app.models.hospital import HospitalStaff
    from app.models.money import Settlement


class ConfirmationRequest(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A one-time code sent to the hospital's official register contact. Only an HMAC is kept."""

    __tablename__ = "confirmation_requests"
    __table_args__ = (
        CheckConstraint("purpose IN ('decision', 'second_approval')", name="purpose_valid"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"))
    requested_by_staff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_staff.id", ondelete="RESTRICT")
    )
    purpose: Mapped[str] = mapped_column(String(20))
    channel: Mapped[OtpChannel] = mapped_column(
        pg_enum(OtpChannel), default=OtpChannel.EMAIL, server_default=OtpChannel.EMAIL.value
    )
    destination_masked: Mapped[str] = mapped_column(String(80))
    otp_hmac: Mapped[str] = mapped_column(CHAR(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    status: Mapped[ConfirmationRequestStatus] = mapped_column(
        pg_enum(ConfirmationRequestStatus),
        default=ConfirmationRequestStatus.SENT,
        server_default=ConfirmationRequestStatus.SENT.value,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped["Case"] = relationship(back_populates="confirmation_requests")
    requested_by: Mapped["HospitalStaff"] = relationship(back_populates="confirmation_requests")
    confirmation: Mapped["Confirmation | None"] = relationship(
        back_populates="confirmation_request",
        foreign_keys="Confirmation.confirmation_request_id",
        uselist=False,
    )
    second_approval_for: Mapped["Confirmation | None"] = relationship(
        back_populates="second_request",
        foreign_keys="Confirmation.second_request_id",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<ConfirmationRequest id={self.id} purpose={self.purpose} status={self.status}>"


class Confirmation(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """A hospital's decision on a bill. A confirmed one is the gate for settlement."""

    __tablename__ = "confirmations"
    __table_args__ = (
        CheckConstraint(
            "second_approver_staff_id IS NULL OR second_approver_staff_id <> confirmed_by_staff_id",
            name="second_approver_differs",
        ),
        CheckConstraint("amount_kes_minor > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('queried', 'rejected') OR amount_kes_minor IS NOT NULL",
            name="amount_required",
        ),
        CheckConstraint("deposit_kes_minor >= 0", name="deposit_non_negative"),
        CheckConstraint(
            "status <> 'confirmed' OR finalized_at IS NOT NULL", name="confirmed_has_finalized_at"
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id", ondelete="RESTRICT"))
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("case_documents.id", ondelete="RESTRICT")
    )
    confirmation_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("confirmation_requests.id", ondelete="RESTRICT"), unique=True
    )
    confirmed_by_staff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_staff.id", ondelete="RESTRICT")
    )
    second_approver_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_staff.id", ondelete="RESTRICT")
    )
    second_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("confirmation_requests.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[ConfirmationStatus] = mapped_column(pg_enum(ConfirmationStatus))
    amount_kes_minor: Mapped[int | None] = mapped_column(BigInteger)
    invoice_number: Mapped[str | None] = mapped_column(String(60))
    is_final_bill: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    deposit_kes_minor: Mapped[int | None] = mapped_column(BigInteger)
    query_note: Mapped[str | None] = mapped_column(String(500))
    paypal_invoice_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped["Case"] = relationship(back_populates="confirmations")
    document: Mapped["CaseDocument"] = relationship(back_populates="confirmations")
    confirmation_request: Mapped[ConfirmationRequest] = relationship(
        back_populates="confirmation", foreign_keys=[confirmation_request_id]
    )
    second_request: Mapped[ConfirmationRequest | None] = relationship(
        back_populates="second_approval_for", foreign_keys=[second_request_id]
    )
    confirmed_by: Mapped["HospitalStaff"] = relationship(
        back_populates="confirmations", foreign_keys=[confirmed_by_staff_id]
    )
    second_approver: Mapped["HospitalStaff | None"] = relationship(
        back_populates="second_approvals", foreign_keys=[second_approver_staff_id]
    )
    settlements: Mapped[list["Settlement"]] = relationship(back_populates="confirmation")

    def __repr__(self) -> str:
        return f"<Confirmation id={self.id} status={self.status}>"
