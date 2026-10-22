"""The facility register, onboarded hospitals and their billing staff."""

import re
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SettlementMethod, StaffRole, pg_enum
from app.models.types import EncryptedBytes

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.identity import User
    from app.models.verification import Confirmation, ConfirmationRequest

# 200,000 KES in KES cents: the default amount above which two staff members must approve.
DEFAULT_TWO_PERSON_THRESHOLD_KES_MINOR = 20_000_000


def normalize_facility_name(name: str) -> str:
    """Lowercase, punctuation removed, single spaces: the form used for fuzzy matching."""
    cleaned = re.sub(r"[^\w\s]", " ", name.casefold())
    return " ".join(cleaned.split())


class FacilityRegisterEntry(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A row of the facility register (synthetic, fictional names for the hackathon).

    ``official_email`` is the only channel that receives hospital confirmation codes.
    """

    __tablename__ = "facility_register_entries"

    register_code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    normalized_name: Mapped[str] = mapped_column(String(160), index=True)
    county: Mapped[str] = mapped_column(String(40))
    facility_level: Mapped[str | None] = mapped_column(String(20))
    ownership: Mapped[str | None] = mapped_column(String(20))
    official_email: Mapped[str] = mapped_column(String(160))
    official_phone: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(80))

    hospital: Mapped["Hospital | None"] = relationship(
        back_populates="register_entry", uselist=False
    )

    def __repr__(self) -> str:
        return f"<FacilityRegisterEntry {self.register_code}>"


class Hospital(db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """A register entry that has been onboarded onto TibaDirect."""

    __tablename__ = "hospitals"
    __table_args__ = (
        CheckConstraint(
            "two_person_threshold_kes_minor >= 0", name="two_person_threshold_non_negative"
        ),
    )

    register_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("facility_register_entries.id", ondelete="RESTRICT"), unique=True
    )
    display_name: Mapped[str] = mapped_column(String(160))
    county: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    settlement_method: Mapped[SettlementMethod] = mapped_column(
        pg_enum(SettlementMethod),
        default=SettlementMethod.PAYPAL_PAYOUTS,
        server_default=SettlementMethod.PAYPAL_PAYOUTS.value,
    )
    paypal_payout_email: Mapped[str | None] = mapped_column(String(160))
    settlement_account_ref_enc: Mapped[bytes | None] = mapped_column(EncryptedBytes)
    two_person_threshold_kes_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=DEFAULT_TWO_PERSON_THRESHOLD_KES_MINOR,
        server_default=str(DEFAULT_TWO_PERSON_THRESHOLD_KES_MINOR),
    )

    register_entry: Mapped[FacilityRegisterEntry] = relationship(back_populates="hospital")
    staff: Mapped[list["HospitalStaff"]] = relationship(back_populates="hospital")
    cases: Mapped[list["Case"]] = relationship(back_populates="hospital")
    confirmations: Mapped[list["Confirmation"]] = relationship(back_populates="hospital")

    @property
    def official_email(self) -> str:
        """The register's official contact, the only destination for confirmation codes."""
        return self.register_entry.official_email

    def __repr__(self) -> str:
        return f"<Hospital id={self.id}>"


class HospitalStaff(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A billing clerk or supervisor. A user belongs to at most one hospital."""

    __tablename__ = "hospital_staff"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True
    )
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospitals.id", ondelete="RESTRICT"), index=True
    )
    staff_role: Mapped[StaffRole] = mapped_column(pg_enum(StaffRole))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    user: Mapped["User"] = relationship(back_populates="staff_profile")
    hospital: Mapped[Hospital] = relationship(back_populates="staff")
    confirmation_requests: Mapped[list["ConfirmationRequest"]] = relationship(
        back_populates="requested_by"
    )
    confirmations: Mapped[list["Confirmation"]] = relationship(
        back_populates="confirmed_by", foreign_keys="Confirmation.confirmed_by_staff_id"
    )
    second_approvals: Mapped[list["Confirmation"]] = relationship(
        back_populates="second_approver", foreign_keys="Confirmation.second_approver_staff_id"
    )

    def __repr__(self) -> str:
        return f"<HospitalStaff id={self.id} role={self.staff_role}>"
