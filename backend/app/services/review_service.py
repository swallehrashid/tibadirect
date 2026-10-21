"""The human review queue (s.35): the automatic checks flag, a person decides, with a reason.

Items come from the AI checks, from a family asking for a second look, from a hospital's query
and from staff anomalies. Resolving one needs a decision and a written note; the note is stored
with the item and never copied into the audit trail.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.errors import Conflict, NotFound, ValidationFailed
from app.models.case import Case, ReviewItem
from app.models.enums import (
    CaseStatus,
    ReviewResolution,
    ReviewSource,
    ReviewStatus,
)
from app.models.identity import User
from app.services import audit_service, case_service

MESSAGE_LENGTH = (10, 500)
NOTE_LENGTH = (10, 500)
# A family may ask for a review once the case is submitted and until it ends.
REQUESTABLE_STATUSES = frozenset(
    {
        CaseStatus.PENDING_TRIAGE,
        CaseStatus.IN_REVIEW,
        CaseStatus.PENDING_CONFIRMATION,
        CaseStatus.LIVE,
        CaseStatus.FUNDED,
    }
)


def create_item(
    session: Session,
    case: Case,
    source: ReviewSource,
    reason: str,
    payload: dict[str, Any] | None = None,
) -> ReviewItem:
    """Open a review item. The caller commits."""
    item = ReviewItem(case=case, source=source, reason=reason[:300], payload=payload or {})
    session.add(item)
    session.flush()
    return item


def request_review(session: Session, case: Case, user: User, message: str) -> ReviewItem:
    """The family asks for a person to look at their case. One open request per case."""
    text = " ".join(message.split())
    if not MESSAGE_LENGTH[0] <= len(text) <= MESSAGE_LENGTH[1]:
        raise ValidationFailed(
            "Tell us in 10 to 500 characters what you would like us to look at.",
            code="message_length",
            details={"json": {"message": ["Use between 10 and 500 characters."]}},
        )
    if case.status not in REQUESTABLE_STATUSES:
        raise Conflict(
            "A review can be requested once the case is submitted and until it ends.",
            code="case_not_reviewable",
        )
    already = session.execute(
        select(ReviewItem.id).where(
            ReviewItem.case_id == case.id,
            ReviewItem.source == ReviewSource.FAMILY_REQUEST,
            ReviewItem.status == ReviewStatus.OPEN,
        )
    ).first()
    if already:
        raise Conflict(
            "You already asked for a review. We will look at it soon.",
            code="review_already_requested",
        )
    item = create_item(
        session,
        case,
        ReviewSource.FAMILY_REQUEST,
        "The family asked for a review",
        {"message": text},
    )
    audit_service.record("review.requested", case, actor=user, details={"review_item_id": item.id})
    return item


def queue(status: ReviewStatus | None = None) -> Select[tuple[ReviewItem]]:
    """Review items: open ones first, oldest first (longest wait on top); resolved newest first."""
    statement = select(ReviewItem)
    if status is not None:
        statement = statement.where(ReviewItem.status == status)
    open_first = ReviewItem.status == ReviewStatus.OPEN
    return statement.order_by(
        open_first.desc(),
        ReviewItem.created_at.asc()
        if status != ReviewStatus.RESOLVED
        else ReviewItem.created_at.desc(),
        ReviewItem.id,
    )


def get_or_404(session: Session, item_id: uuid.UUID) -> ReviewItem:
    item = session.get(ReviewItem, item_id)
    if item is None:
        raise NotFound("Review item not found.", code="review_item_not_found")
    return item


def resolve(
    session: Session,
    item: ReviewItem,
    admin: User,
    resolution: ReviewResolution,
    note: str,
) -> ReviewItem:
    """Close an item with a decision and a written reason. The caller commits.

    Approving for confirmation moves an in-review case on to the hospital; cancelling ends it;
    the other two leave the case as it is. A decision the case's status cannot take is refused
    and nothing is recorded.
    """
    if item.status == ReviewStatus.RESOLVED:
        raise Conflict("This item was already resolved.", code="review_item_resolved")
    cleaned = " ".join(note.split())
    if not NOTE_LENGTH[0] <= len(cleaned) <= NOTE_LENGTH[1]:
        raise ValidationFailed(
            "Write why, in 10 to 500 characters.",
            code="note_required",
            details={"json": {"note": ["Write why, in 10 to 500 characters."]}},
        )
    case = item.case
    if resolution == ReviewResolution.APPROVED_FOR_CONFIRMATION:
        if case.status != CaseStatus.IN_REVIEW:
            raise Conflict(
                "Only a case that is in review can be approved for confirmation.",
                code="case_not_in_review",
            )
        case_service.transition(case, CaseStatus.PENDING_CONFIRMATION, admin, "review_approved")
    elif resolution == ReviewResolution.CASE_CANCELLED:
        case_service.transition(case, CaseStatus.CANCELLED, admin, "review_cancelled")

    item.status = ReviewStatus.RESOLVED
    item.resolution = resolution
    item.resolution_note = cleaned
    item.resolved_by_id = admin.id
    item.resolved_at = datetime.now(UTC)
    session.flush()
    audit_service.record(
        "review.resolved",
        case,
        actor=admin,
        details={
            "review_item_id": item.id,
            "resolution": resolution,
            "source": item.source,
        },
    )
    return item
