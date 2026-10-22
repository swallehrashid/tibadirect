"""FX rates, donations, settlements, their allocations and refunds.

All amounts are integer minor units: ``*_usd_minor`` is US cents and ``*_kes_minor`` is KES cents.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ActorType,
    DonationStatus,
    ExcessChoice,
    RefundReason,
    RefundStatus,
    SettlementMethod,
    SettlementStatus,
    pg_enum,
)
from app.models.types import EncryptedBytes
from app.security import crypto

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.verification import Confirmation

MIN_DONATION_USD_MINOR = 100  # $1.00
MAX_DONATION_USD_MINOR = 1_000_000  # $10,000.00


class FxRate(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A USD to KES rate: ``rate`` is KES per 1 USD."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        CheckConstraint("rate > 0", name="rate_positive"),
        Index("ix_fx_rates_pair_fetched_at", "base", "quote", text("fetched_at DESC")),
    )

    base: Mapped[str] = mapped_column(CHAR(3))
    quote: Mapped[str] = mapped_column(CHAR(3))
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    source: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    donations: Mapped[list["Donation"]] = relationship(back_populates="fx_rate")
    settlements: Mapped[list["Settlement"]] = relationship(back_populates="fx_rate")

    def __repr__(self) -> str:
        return f"<FxRate {self.base}/{self.quote} {self.rate}>"


class Donation(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """One PayPal donation. The donor's email is encrypted and matched through a keyed hash."""

    __tablename__ = "donations"
    __table_args__ = (
        CheckConstraint(
            f"amount_usd_minor BETWEEN {MIN_DONATION_USD_MINOR} AND {MAX_DONATION_USD_MINOR}",
            name="amount_range",
        ),
        CheckConstraint("paypal_fee_usd_minor >= 0", name="fee_non_negative"),
        CheckConstraint("net_usd_minor >= 0", name="net_non_negative"),
        CheckConstraint("allocated_usd_minor >= 0", name="allocated_non_negative"),
        CheckConstraint("refunded_usd_minor >= 0", name="refunded_non_negative"),
        CheckConstraint("pooled_usd_minor >= 0", name="pooled_non_negative"),
        CheckConstraint(
            "allocated_usd_minor + refunded_usd_minor + pooled_usd_minor <= net_usd_minor",
            name="allocation_within_net",
        ),
        Index("ix_donations_case_status", "case_id", "status"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    paypal_order_id: Mapped[str] = mapped_column(String(40), unique=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    status: Mapped[DonationStatus] = mapped_column(
        pg_enum(DonationStatus),
        default=DonationStatus.CREATED,
        server_default=DonationStatus.CREATED.value,
    )
    amount_usd_minor: Mapped[int] = mapped_column(BigInteger)
    paypal_fee_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    net_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    fx_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fx_rates.id", ondelete="RESTRICT")
    )
    kes_equivalent_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    allocated_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    refunded_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    pooled_usd_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    excess_choice: Mapped[ExcessChoice] = mapped_column(
        pg_enum(ExcessChoice),
        default=ExcessChoice.REFUND,
        server_default=ExcessChoice.REFUND.value,
    )
    donor_display_name: Mapped[str | None] = mapped_column(String(40))
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    donor_email_enc: Mapped[bytes | None] = mapped_column(EncryptedBytes)
    donor_email_hmac: Mapped[str | None] = mapped_column(CHAR(64), index=True)
    receipt_token: Mapped[str] = mapped_column(String(32), unique=True, default=crypto.new_token)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped["Case"] = relationship(back_populates="donations")
    fx_rate: Mapped[FxRate | None] = relationship(back_populates="donations")
    allocations: Mapped[list["SettlementAllocation"]] = relationship(back_populates="donation")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="donation")

    def __repr__(self) -> str:
        return f"<Donation id={self.id} status={self.status}>"


class Settlement(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """One tranche paid to the hospital. ``idempotency_key`` makes a retry safe."""

    __tablename__ = "settlements"
    __table_args__ = (
        UniqueConstraint("case_id", "tranche_no", name="uq_settlements_case_id_tranche_no"),
        CheckConstraint("amount_usd_minor > 0", name="amount_usd_positive"),
        CheckConstraint("amount_kes_minor > 0", name="amount_kes_positive"),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    confirmation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("confirmations.id", ondelete="RESTRICT")
    )
    tranche_no: Mapped[int] = mapped_column(SmallInteger)
    adapter: Mapped[SettlementMethod] = mapped_column(pg_enum(SettlementMethod))
    status: Mapped[SettlementStatus] = mapped_column(
        pg_enum(SettlementStatus),
        default=SettlementStatus.PENDING,
        server_default=SettlementStatus.PENDING.value,
    )
    amount_usd_minor: Mapped[int] = mapped_column(BigInteger)
    amount_kes_minor: Mapped[int] = mapped_column(BigInteger)
    fx_rate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fx_rates.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    external_batch_id: Mapped[str | None] = mapped_column(String(40))
    external_item_id: Mapped[str | None] = mapped_column(String(40))
    invoice_payment_id: Mapped[str | None] = mapped_column(String(40))
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped["Case"] = relationship(back_populates="settlements")
    confirmation: Mapped["Confirmation"] = relationship(back_populates="settlements")
    fx_rate: Mapped[FxRate] = relationship(back_populates="settlements")
    allocations: Mapped[list["SettlementAllocation"]] = relationship(back_populates="settlement")

    def __repr__(self) -> str:
        return f"<Settlement id={self.id} tranche={self.tranche_no} status={self.status}>"


class SettlementAllocation(db.Model, CreatedAtMixin):
    """How much of one donation a settlement draws on (first in, first out by capture time)."""

    __tablename__ = "settlement_allocations"
    __table_args__ = (CheckConstraint("amount_usd_minor > 0", name="amount_positive"),)

    settlement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("settlements.id", ondelete="RESTRICT"), primary_key=True
    )
    donation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("donations.id", ondelete="RESTRICT"), primary_key=True
    )
    amount_usd_minor: Mapped[int] = mapped_column(BigInteger)

    settlement: Mapped[Settlement] = relationship(back_populates="allocations")
    donation: Mapped[Donation] = relationship(back_populates="allocations")

    def __repr__(self) -> str:
        return f"<SettlementAllocation settlement={self.settlement_id} donation={self.donation_id}>"


class Refund(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """A refund of all or part of a donation. ``idempotency_key`` is the PayPal request ID."""

    __tablename__ = "refunds"
    __table_args__ = (CheckConstraint("amount_usd_minor > 0", name="amount_positive"),)

    donation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("donations.id", ondelete="RESTRICT"), index=True
    )
    amount_usd_minor: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[RefundReason] = mapped_column(pg_enum(RefundReason))
    status: Mapped[RefundStatus] = mapped_column(
        pg_enum(RefundStatus),
        default=RefundStatus.PENDING,
        server_default=RefundStatus.PENDING.value,
    )
    paypal_refund_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    requested_by: Mapped[ActorType] = mapped_column(pg_enum(ActorType))
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    donation: Mapped[Donation] = relationship(back_populates="refunds")

    def __repr__(self) -> str:
        return f"<Refund id={self.id} status={self.status}>"
