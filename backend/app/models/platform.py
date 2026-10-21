"""Appeal checks, the audit log and the treasurer agent's proposed actions."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ActorType,
    AgentActionStatus,
    AgentActionType,
    AppealInputKind,
    RiskBand,
    pg_enum,
)

if TYPE_CHECKING:
    from app.models.case import Case

_EMPTY_JSON = text("'{}'::jsonb")


class AppealCheck(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """The result of checking a forwarded appeal. The pasted text or image is never stored,
    only an HMAC of it."""

    __tablename__ = "appeal_checks"
    __table_args__ = (CheckConstraint("risk_score BETWEEN 0 AND 100", name="risk_score_range"),)

    input_hmac: Mapped[str] = mapped_column(CHAR(64), index=True)
    input_kind: Mapped[AppealInputKind] = mapped_column(pg_enum(AppealInputKind))
    risk_score: Mapped[int] = mapped_column(SmallInteger)
    risk_band: Mapped[RiskBand] = mapped_column(pg_enum(RiskBand))
    reasons_en: Mapped[list[Any]] = mapped_column(JSONB(none_as_null=True))
    reasons_sw: Mapped[list[Any]] = mapped_column(JSONB(none_as_null=True))
    matched_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT")
    )
    used_llm: Mapped[bool] = mapped_column(Boolean)

    matched_case: Mapped["Case | None"] = relationship(back_populates="appeal_checks")

    def __repr__(self) -> str:
        return f"<AppealCheck id={self.id} band={self.risk_band}>"


class AuditLog(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Who did what to which record. ``details`` holds no personal data or document text."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_target", "target_type", "target_id"),)

    actor_type: Mapped[ActorType] = mapped_column(pg_enum(ActorType))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[uuid.UUID | None] = mapped_column()
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), default=dict, server_default=_EMPTY_JSON
    )
    request_id: Mapped[str | None] = mapped_column(String(36))
    ip_hmac: Mapped[str | None] = mapped_column(CHAR(64))

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action}>"


class AgentAction(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """An action the treasurer agent proposed. Nothing beyond ``proposed`` exists without the
    admin who decided it."""

    __tablename__ = "agent_actions"
    __table_args__ = (
        CheckConstraint(
            "status = 'proposed' OR (decided_by_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="decided_by_a_human",
        ),
    )

    action_type: Mapped[AgentActionType] = mapped_column(pg_enum(AgentActionType))
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    rationale: Mapped[str] = mapped_column(Text)
    status: Mapped[AgentActionStatus] = mapped_column(
        pg_enum(AgentActionStatus),
        default=AgentActionStatus.PROPOSED,
        server_default=AgentActionStatus.PROPOSED.value,
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[str | None] = mapped_column(String(500))

    case: Mapped["Case | None"] = relationship(back_populates="agent_actions")

    def __repr__(self) -> str:
        return f"<AgentAction id={self.id} type={self.action_type} status={self.status}>"
