"""Case lifecycle rules.

``transition`` is the only place a case changes status. It checks the move against the state
machine, keeps ``trust_level`` and the timestamps in step, and writes the audit entry. Creating
and editing drafts, totals and the public views are added by the case endpoints (P2-F07).
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from flask import current_app
from sqlalchemy import Select, exists, func, select
from sqlalchemy.orm import Session

from app import lexicon
from app.errors import Conflict, NotFound, ValidationFailed
from app.logging import scrub
from app.models.case import Case, Consent, TriageResult
from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    ConfirmationStatus,
    ConsentActor,
    ConsentScope,
    DonationStatus,
    SettlementStatus,
    TrustLevel,
)
from app.models.hospital import Hospital
from app.models.identity import User
from app.models.platform import AuditLog
from app.models.verification import Confirmation
from app.money import apply_bps, usd_minor_to_kes_minor
from app.services import audit_service

S = CaseStatus

# The lifecycle diagram from the design: each status and where it may go next.
ALLOWED_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    S.DRAFT: frozenset({S.PENDING_TRIAGE, S.CANCELLED}),
    S.PENDING_TRIAGE: frozenset({S.PENDING_CONFIRMATION, S.IN_REVIEW, S.FROZEN, S.CANCELLED}),
    S.IN_REVIEW: frozenset({S.PENDING_CONFIRMATION, S.CANCELLED, S.FROZEN}),
    S.PENDING_CONFIRMATION: frozenset({S.LIVE, S.IN_REVIEW, S.CANCELLED, S.FROZEN}),
    S.LIVE: frozenset({S.FUNDED, S.CLOSED, S.CANCELLED, S.FROZEN}),
    S.FUNDED: frozenset({S.LIVE, S.CLOSED, S.CANCELLED, S.FROZEN}),
    S.FROZEN: frozenset({S.CANCELLED}),
    S.CLOSED: frozenset(),
    S.CANCELLED: frozenset(),
}

# Statuses in which a family may still change the case or give consent.
OPEN_STATUSES = frozenset(
    {S.DRAFT, S.PENDING_TRIAGE, S.IN_REVIEW, S.PENDING_CONFIRMATION, S.LIVE, S.FUNDED}
)
# Statuses before the hospital has confirmed anything.
UNCONFIRMED_STATUSES = frozenset({S.DRAFT, S.PENDING_TRIAGE, S.IN_REVIEW, S.PENDING_CONFIRMATION})


class InvalidTransition(Conflict):
    """The case cannot move from its current status to the requested one."""

    code = "invalid_transition"
    message = "The case cannot move to that status from where it is."


def can_transition(current: CaseStatus, target: CaseStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def _trust_level(case: Case, target: CaseStatus) -> TrustLevel:
    """Trust follows status: confirmed from ``live`` on, and never again after a freeze."""
    if target == S.PENDING_CONFIRMATION:
        return TrustLevel.PENDING_CONFIRMATION
    if target in {S.LIVE, S.FUNDED, S.CLOSED}:
        return TrustLevel.HOSPITAL_CONFIRMED
    if target == S.CANCELLED:
        return case.trust_level
    return TrustLevel.UNVERIFIED  # draft, pending_triage, in_review, frozen


def transition(
    case: Case,
    to: CaseStatus,
    actor: User | ActorType,
    reason: str | None = None,
    *,
    close_reason: CloseReason | None = None,
    approver: User | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> Case:
    """Move ``case`` to ``to`` and audit it. The caller commits.

    ``reason`` is a short code or sentence for the audit trail; for a freeze it is also stored on
    the case and is required. Closing needs a ``close_reason``. Going live needs an active
    ``public_page`` consent. Anything the diagram does not allow raises ``InvalidTransition``.
    """
    current = case.status
    if not can_transition(current, to):
        raise InvalidTransition(
            f"A case that is {current.value} cannot become {to.value}.",
            details={"from": current.value, "to": to.value},
        )
    if to == S.CLOSED and close_reason is None:
        raise InvalidTransition(
            "Closing a case needs a close reason.", code="close_reason_required"
        )
    if to == S.FROZEN and not (reason and reason.strip()):
        raise InvalidTransition("Freezing a case needs a reason.", code="freeze_reason_required")
    if (
        current == S.PENDING_CONFIRMATION
        and to == S.LIVE
        and case.active_consent(ConsentScope.PUBLIC_PAGE) is None
    ):
        raise InvalidTransition(
            "The family has not agreed to a public page, so the case cannot go live.",
            code="public_consent_required",
        )

    stamp = now or datetime.now(UTC)
    case.status = to
    case.trust_level = _trust_level(case, to)
    if current == S.DRAFT and to == S.PENDING_TRIAGE:
        case.submitted_at = stamp
    if to == S.LIVE and case.live_at is None:
        case.live_at = stamp
    if to == S.FUNDED:
        case.funded_at = stamp
    if current == S.FUNDED and to == S.LIVE:
        case.funded_at = None
    if to == S.CLOSED:
        case.closed_at = stamp
        case.close_reason = close_reason
    if to == S.FROZEN:
        case.frozen_at = stamp
        case.freeze_reason = (reason or "").strip()[:300]

    details: dict[str, Any] = {"from": current, "to": to}
    if reason and to != S.FROZEN:
        details["reason"] = reason
    audit_service.record(
        "case.transitioned",
        case,
        actor=actor,
        approver=approver,
        details=details,
        request_id=request_id,
    )
    return case


# --------------------------------------------------------------------------- drafts


ALIAS_LENGTH = (2, 40)
REGION_LENGTH = (2, 40)
STORY_MAX = 600
PATIENT_REF_LENGTH = (3, 60)
_PATIENT_REF_CHARS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 /._#-]*$")
_REGION_CHARS = re.compile(r"^[^\W\d_][^\W\d_ '’.-]*(?:[ '’.-]+[^\W\d_][^\W\d_ '’.-]*)*$")

EDITABLE_FIELDS = ("alias", "region", "story", "is_minor", "patient_reference", "show_photo")


def _field_error(field: str, message: str, code: str, **extra: Any) -> ValidationFailed:
    """A 422 shaped like a schema error (``details.json.<field>``) with a specific code."""
    return ValidationFailed(message, code=code, details={"json": {field: [message]}, **extra})


def clean_alias(value: str) -> str:
    """The public name of the case: short, and free of personal data and medical words."""
    alias = " ".join(value.split())
    if not ALIAS_LENGTH[0] <= len(alias) <= ALIAS_LENGTH[1]:
        raise _field_error("alias", "Use between 2 and 40 characters.", "alias_length")
    if scrub(alias) != alias:
        raise _field_error(
            "alias",
            "Do not put an email address or phone number in the alias.",
            "alias_personal_data",
        )
    if terms := lexicon.find_medical_terms(alias):
        raise _field_error(
            "alias",
            "Do not describe the illness in the alias.",
            "alias_medical_terms",
            terms=terms,
        )
    return alias


def clean_region(value: str) -> str:
    region = " ".join(value.split())
    if not REGION_LENGTH[0] <= len(region) <= REGION_LENGTH[1] or not _REGION_CHARS.match(region):
        raise _field_error("region", "Enter a county or town, 2 to 40 letters.", "region_invalid")
    return region


def clean_patient_reference(value: str) -> str:
    """The hospital's admission or patient number, as printed on the bill."""
    reference = " ".join(value.split())
    if not PATIENT_REF_LENGTH[0] <= len(reference) <= PATIENT_REF_LENGTH[1] or not (
        _PATIENT_REF_CHARS.match(reference)
    ):
        raise _field_error(
            "patient_reference",
            "Enter the admission or patient number from the bill (3 to 60 characters).",
            "patient_reference_invalid",
        )
    return reference


def clean_story(value: str | None) -> str | None:
    """The family's words on the public page: no medical terms, no contact details."""
    story = (value or "").strip()
    if not story:
        return None
    if len(story) > STORY_MAX:
        raise _field_error("story", f"Use at most {STORY_MAX} characters.", "story_length")
    if scrub(story) != story:
        raise _field_error(
            "story",
            "Do not put an email address, phone number or long number in the story.",
            "story_personal_data",
        )
    if terms := lexicon.find_medical_terms(story):
        raise _field_error(
            "story",
            "Please leave out medical words. The hospital bill already shows the care given.",
            "story_medical_terms",
            terms=terms[:10],
        )
    return story


def _active_hospital(session: Session, hospital_id: uuid.UUID) -> Hospital:
    hospital = session.get(Hospital, hospital_id)
    if hospital is None or not hospital.is_active:
        raise _field_error("hospital_id", "Choose a hospital from the list.", "hospital_not_found")
    return hospital


def create_draft(
    session: Session,
    user: User,
    *,
    hospital_id: uuid.UUID,
    alias: str,
    region: str,
    patient_reference: str,
    is_minor: bool = False,
) -> Case:
    """Start a new draft case for ``user``. The caller commits."""
    hospital = _active_hospital(session, hospital_id)
    case = Case(
        created_by=user,
        hospital=hospital,
        alias=clean_alias(alias),
        region=clean_region(region),
        is_minor=is_minor,
        status=CaseStatus.DRAFT,
        buffer_bps=current_app.config["BUFFER_BPS_DEFAULT"],
    )
    case.set_patient_ref(clean_patient_reference(patient_reference))
    session.add(case)
    session.flush()
    audit_service.record("case.created", case, actor=user, details={"hospital_id": hospital.id})
    return case


def update_draft(session: Session, case: Case, user: User, changes: dict[str, Any]) -> Case:
    """Apply the family's edits to a draft. Only drafts are editable; the caller commits."""
    if case.status != CaseStatus.DRAFT:
        raise Conflict("Only a draft can be edited.", code="case_not_editable")
    if not changes:
        return case

    cleaners = {
        "alias": clean_alias,
        "region": clean_region,
        "story": clean_story,
        "patient_reference": clean_patient_reference,
    }
    values = {
        field: cleaners[field](value) if field in cleaners else value
        for field, value in changes.items()
    }

    is_minor = values.get("is_minor", case.is_minor)
    if values.get("is_minor") is True and not case.is_minor:
        for consent in case.consents:
            if consent.withdrawn_at is None and consent.actor != ConsentActor.GUARDIAN:
                raise Conflict(
                    "A child's case needs the parent or guardian's consent. Withdraw the consent "
                    "already given and give it again as the guardian.",
                    code="guardian_consent_required",
                )
    if is_minor and values.get("show_photo") is True:
        raise _field_error(
            "show_photo",
            "A child's photo is never shown on the public page.",
            "photo_not_allowed_for_minor",
        )

    for field, value in values.items():
        if field == "patient_reference":
            case.set_patient_ref(value)
        else:
            setattr(case, field, value)
    if is_minor:
        case.show_photo = False
    session.flush()
    audit_service.record("case.updated", case, actor=user, details={"fields": sorted(changes)})
    return case


def delete_draft(case: Case, user: User) -> Case:
    """Cancel a draft the family no longer wants. Only drafts can be deleted."""
    if case.status != CaseStatus.DRAFT:
        raise Conflict("Only a draft can be deleted.", code="case_not_draft")
    return transition(case, CaseStatus.CANCELLED, user, "draft_deleted")


# --------------------------------------------------------------------------- money


def recompute_totals(case: Case) -> None:
    """Set the cached money columns from the donations and settlements that back them.

    Money moves through donation, settlement and refund code that updates these columns as it
    goes; this recomputes them from the rows, and tests compare the two after every operation.
    """
    counted = {DonationStatus.CAPTURED, DonationStatus.PARTIALLY_REFUNDED, DonationStatus.REFUNDED}
    donations = [d for d in case.donations if d.status in counted]
    case.captured_net_usd_minor = sum(d.net_usd_minor for d in donations)
    case.refunded_usd_minor = sum(d.refunded_usd_minor for d in donations)
    case.pledged_kes_minor = sum(
        usd_minor_to_kes_minor(d.net_usd_minor - d.refunded_usd_minor, d.fx_rate.rate)
        for d in donations
    )
    done = [s for s in case.settlements if s.status == SettlementStatus.SUCCEEDED]
    case.settled_usd_minor = sum(s.amount_usd_minor for s in done)
    case.settled_kes_minor = sum(s.amount_kes_minor for s in done)
    case.donor_count = len(donations)


def goal_kes_minor(case: Case) -> int | None:
    """What the case may raise in KES: the confirmed target plus its FX and fee buffer."""
    if case.target_kes_minor is None:
        return None
    return apply_bps(case.target_kes_minor, 10_000 + case.buffer_bps)


def lock_case(session: Session, case_id: uuid.UUID) -> Case:
    """The case row, locked and freshly read.

    Every money path (capture, webhook, settlement, refund) takes this lock first and only then
    locks donations, settlements or refunds, so no two of them can wait on each other.
    """
    return session.execute(
        select(Case)
        .where(Case.id == case_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def has_reached_goal(case: Case) -> bool:
    """Pledged money covers the confirmed target plus its buffer."""
    goal = goal_kes_minor(case)
    return goal is not None and case.pledged_kes_minor >= goal


def sync_funding(case: Case, actor: User | ActorType = ActorType.SYSTEM) -> CaseStatus | None:
    """Keep ``live`` and ``funded`` in step with the money. Returns the new status if it moved.

    A live case whose pledges cover its goal becomes funded and stops taking donations; a funded
    case whose pledges fall below the goal (a refund, a reversal, or a larger bill) is live again.
    Call it after anything that changes the pledged total or the target. The caller commits.
    """
    reached = has_reached_goal(case)
    if case.status == CaseStatus.LIVE and reached:
        transition(case, CaseStatus.FUNDED, actor, "goal_reached")
        return CaseStatus.FUNDED
    if case.status == CaseStatus.FUNDED and not reached:
        transition(case, CaseStatus.LIVE, actor, "below_goal")
        return CaseStatus.LIVE
    return None


def final_bill_was_lower(case: Case) -> bool:
    """True if the latest confirmed bill is for less than the first one the hospital confirmed."""
    confirmed = sorted(
        (c for c in case.confirmations if c.status == ConfirmationStatus.CONFIRMED),
        key=lambda c: (c.finalized_at or c.created_at, c.created_at),
    )
    if len(confirmed) < 2:
        return False
    return (confirmed[-1].amount_kes_minor or 0) < (confirmed[0].amount_kes_minor or 0)


def close_if_fully_settled(case: Case, actor: User | ActorType = ActorType.SYSTEM) -> bool:
    """Close the case once a final bill is confirmed and all of it has reached the hospital.

    Closed with ``final_bill_lower`` if the final bill was below the first estimate, otherwise
    ``fully_settled``. A tranche still on its way keeps the case open. Returns True if it closed.
    The caller commits.
    """
    if case.status not in (CaseStatus.LIVE, CaseStatus.FUNDED):
        return False
    if case.is_estimate or case.target_kes_minor is None:
        return False
    if case.settled_kes_minor < case.target_kes_minor:
        return False
    if any(
        s.status in (SettlementStatus.PENDING, SettlementStatus.PROCESSING)
        for s in case.settlements
    ):
        return False
    reason = (
        CloseReason.FINAL_BILL_LOWER if final_bill_was_lower(case) else CloseReason.FULLY_SETTLED
    )
    transition(case, CaseStatus.CLOSED, actor, reason.value, close_reason=reason)
    return True


def is_accepting_donations(case: Case) -> bool:
    """Live, with a confirmed target, and not yet pledged up to target plus buffer."""
    goal = goal_kes_minor(case)
    return case.status == CaseStatus.LIVE and goal is not None and case.pledged_kes_minor < goal


# --------------------------------------------------------------------------- reading


def status_timeline(session: Session, case: Case) -> list[dict[str, Any]]:
    """Each status the case has been in and when it got there, oldest first."""
    entries = session.execute(
        select(AuditLog)
        .where(
            AuditLog.target_type == "case",
            AuditLog.target_id == case.id,
            AuditLog.action == "case.transitioned",
        )
        .order_by(AuditLog.created_at, AuditLog.id)
    ).scalars()
    timeline = [{"status": CaseStatus.DRAFT.value, "at": case.created_at}]
    timeline.extend({"status": entry.details["to"], "at": entry.created_at} for entry in entries)
    return timeline


def latest_triage(case: Case) -> TriageResult | None:
    """The newest triage result across the case's documents, if any."""
    return max(case.triage_results, key=lambda result: result.created_at, default=None)


def confirmations_newest_first(case: Case) -> list[Confirmation]:
    return sorted(
        case.confirmations, key=lambda confirmation: confirmation.created_at, reverse=True
    )


def public_cases(county: str | None = None) -> Select[tuple[Case]]:
    """Cases that may appear in the public list: live or funded, with the public page consent."""
    statement = (
        select(Case)
        .join(Hospital, Case.hospital_id == Hospital.id)
        .where(
            Case.status.in_([CaseStatus.LIVE, CaseStatus.FUNDED]),
            exists().where(
                Consent.case_id == Case.id,
                Consent.scope == ConsentScope.PUBLIC_PAGE,
                Consent.withdrawn_at.is_(None),
            ),
        )
    )
    if county and county.strip():
        statement = statement.where(func.lower(Hospital.county) == county.strip().lower())
    return statement.order_by(Case.live_at.desc().nulls_last(), Case.id)


def get_public_case_or_404(session: Session, slug: str) -> Case:
    """A case by its public slug, only if its page is public. Otherwise it does not exist."""
    case = session.execute(
        select(Case).where(Case.public_slug == slug.lower())
    ).scalar_one_or_none()
    if case is None or not case.is_publicly_visible:
        raise NotFound("Case not found.", code="case_not_found")
    return case


def public_totals(session: Session) -> dict[str, int]:
    """Landing-page totals over public cases only, so a private case never shows in a number."""
    visible = (
        select(Case.id)
        .where(
            Case.status.in_([CaseStatus.LIVE, CaseStatus.FUNDED, CaseStatus.CLOSED]),
            exists().where(
                Consent.case_id == Case.id,
                Consent.scope == ConsentScope.PUBLIC_PAGE,
                Consent.withdrawn_at.is_(None),
            ),
        )
        .subquery()
    )
    row = session.execute(
        select(
            func.count(Case.id),
            func.count(Case.id).filter(Case.status.in_([CaseStatus.LIVE, CaseStatus.FUNDED])),
            func.coalesce(func.sum(Case.pledged_kes_minor), 0),
            func.coalesce(func.sum(Case.settled_kes_minor), 0),
            func.coalesce(func.sum(Case.donor_count), 0),
        ).where(Case.id.in_(select(visible.c.id)))
    ).one()
    hospitals = session.execute(
        select(func.count()).select_from(Hospital).where(Hospital.is_active.is_(True))
    ).scalar_one()
    return {
        "public_cases": int(row[0]),
        "open_cases": int(row[1]),
        "pledged_kes_minor": int(row[2]),
        "settled_kes_minor": int(row[3]),
        "donations": int(row[4]),
        "hospitals": int(hospitals),
    }
