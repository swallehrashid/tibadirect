"""What the admin console reads and does: totals, the case list and full case view, freezing."""

from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models.case import Case, ReviewItem
from app.models.enums import (
    CaseStatus,
    ConfirmationStatus,
    ReviewStatus,
    UserRole,
)
from app.models.hospital import Hospital
from app.models.identity import User
from app.models.ledger import LedgerEntry
from app.models.platform import AuditLog
from app.models.verification import Confirmation
from app.services import case_ending_service

MAX_QUERY_LENGTH = 80
TRAIL_ENTRIES_SHOWN = 100


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def cases(status: CaseStatus | None = None, query: str | None = None) -> Select[tuple[Case]]:
    """Every case, newest first, optionally by status and by text in the alias, slug, hospital
    name or the owner's email."""
    statement = (
        select(Case)
        .join(Hospital, Case.hospital_id == Hospital.id)
        .join(User, Case.created_by_id == User.id)
    )
    if status is not None:
        statement = statement.where(Case.status == status)
    cleaned = " ".join((query or "").split())[:MAX_QUERY_LENGTH]
    if cleaned:
        pattern = _like(cleaned)
        statement = statement.where(
            or_(
                Case.alias.ilike(pattern, escape="\\"),
                Case.public_slug.ilike(pattern, escape="\\"),
                Hospital.display_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            )
        )
    return statement.order_by(Case.created_at.desc(), Case.id)


def audit_trail(session: Session, case: Case) -> list[AuditLog]:
    """The newest entries about a case, newest first."""
    return list(
        session.execute(
            select(AuditLog)
            .where(AuditLog.target_type == "case", AuditLog.target_id == case.id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id)
            .limit(TRAIL_ENTRIES_SHOWN)
        ).scalars()
    )


def audit_entries(target_type: str | None, target_id: Any | None) -> Select[tuple[AuditLog]]:
    """The audit log, newest first, optionally for one target."""
    statement = select(AuditLog)
    if target_type:
        statement = statement.where(AuditLog.target_type == target_type)
    if target_id is not None:
        statement = statement.where(AuditLog.target_id == target_id)
    return statement.order_by(AuditLog.created_at.desc(), AuditLog.id)


def stats(session: Session) -> dict[str, Any]:
    """Counts by status, money totals and what is waiting for a person."""
    by_status = dict.fromkeys((status.value for status in CaseStatus), 0)
    for status, count in session.execute(select(Case.status, func.count()).group_by(Case.status)):
        by_status[status.value] = count
    money = session.execute(
        select(
            func.coalesce(func.sum(Case.captured_net_usd_minor), 0),
            func.coalesce(func.sum(Case.refunded_usd_minor), 0),
            func.coalesce(func.sum(Case.settled_usd_minor), 0),
            func.coalesce(func.sum(Case.pledged_kes_minor), 0),
            func.coalesce(func.sum(Case.settled_kes_minor), 0),
            func.coalesce(func.sum(Case.donor_count), 0),
        )
    ).one()
    return {
        "cases_by_status": by_status,
        "cases_total": sum(by_status.values()),
        "money": {
            "captured_net_usd_minor": int(money[0]),
            "refunded_usd_minor": int(money[1]),
            "settled_usd_minor": int(money[2]),
            "pledged_kes_minor": int(money[3]),
            "settled_kes_minor": int(money[4]),
            "donations": int(money[5]),
        },
        "waiting": {
            "open_review_items": session.execute(
                select(func.count())
                .select_from(ReviewItem)
                .where(ReviewItem.status == ReviewStatus.OPEN)
            ).scalar_one(),
            "awaiting_second_approval": session.execute(
                select(func.count())
                .select_from(Confirmation)
                .where(Confirmation.status == ConfirmationStatus.AWAITING_SECOND_APPROVAL)
            ).scalar_one(),
        },
        "users": {
            role.value: session.execute(
                select(func.count()).select_from(User).where(User.role == role)
            ).scalar_one()
            for role in UserRole
        },
        "ledger_entries": session.execute(
            select(func.count()).select_from(LedgerEntry)
        ).scalar_one(),
    }


def freeze(session: Session, case: Case, admin: User, reason: str) -> case_ending_service.Ended:
    """Pause a case the admin suspects and start paying the donors back. The caller commits and
    then calls ``case_ending_service.after_commit(result, frozen=True, case=case)``.

    The case stops being public and stops taking money at once and its trust level drops;
    unsent tranches are cancelled and every donor's leftover is refunded, whatever they chose.
    The move is audited by ``transition`` (actor, from and to); the reason is kept on the case.
    """
    return case_ending_service.freeze_case(session, case, admin, reason)
