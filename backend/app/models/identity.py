"""Accounts and sign-in codes."""

from datetime import datetime
from typing import TYPE_CHECKING

from flask_login import UserMixin
from sqlalchemy import CHAR, CheckConstraint, DateTime, SmallInteger, String, true
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserRole, pg_enum

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.hospital import HospitalStaff


class User(UserMixin, db.Model, UUIDPrimaryKeyMixin, TimestampMixin):
    """A person who can sign in. Staff and admin accounts exist only through seed or admin tools."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("preferred_language IN ('en', 'sw')", name="preferred_language_valid"),
    )

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    display_name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(20))
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole), index=True)
    preferred_language: Mapped[str] = mapped_column(String(2), default="en", server_default="en")
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    staff_profile: Mapped["HospitalStaff | None"] = relationship(
        back_populates="user", uselist=False
    )
    cases: Mapped[list["Case"]] = relationship(back_populates="created_by")

    def __repr__(self) -> str:
        return f"<User id={self.id} role={self.role}>"


class LoginCode(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A one-time sign-in code. Only an HMAC of the code is stored, never the code itself."""

    __tablename__ = "login_codes"
    __table_args__ = (CheckConstraint("attempts BETWEEN 0 AND 10", name="attempts_range"),)

    # The code may be for an account that does not exist yet, so there is no foreign key.
    email: Mapped[str] = mapped_column(CITEXT, index=True)
    code_hmac: Mapped[str] = mapped_column(CHAR(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hmac: Mapped[str | None] = mapped_column(CHAR(64))

    def __repr__(self) -> str:
        return f"<LoginCode id={self.id} attempts={self.attempts}>"
