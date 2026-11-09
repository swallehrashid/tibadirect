"""Records of events received from outside systems."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import WebhookStatus, pg_enum


class WebhookEvent(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A PayPal webhook delivery, stored once by event ID so replays change nothing."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        # The sweep that retries events nobody has processed yet looks only at these.
        Index(
            "ix_webhook_events_unprocessed",
            "received_at",
            postgresql_where=text("status = 'received'"),
        ),
    )

    paypal_event_id: Mapped[str] = mapped_column(String(60), unique=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(60), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    signature_verified: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[WebhookStatus] = mapped_column(
        pg_enum(WebhookStatus),
        default=WebhookStatus.RECEIVED,
        server_default=WebhookStatus.RECEIVED.value,
    )
    error: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<WebhookEvent id={self.id} type={self.event_type} status={self.status}>"
