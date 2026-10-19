"""Declarative base, constraint naming convention and column mixins."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db_base import NAMING_CONVENTION, Base


class UUIDPrimaryKeyMixin:
    """UUID primary key, so identifiers in URLs cannot be guessed or enumerated."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    """``created_at`` plus an ``updated_at`` that is refreshed on every update."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = ["NAMING_CONVENTION", "Base", "CreatedAtMixin", "TimestampMixin", "UUIDPrimaryKeyMixin"]
