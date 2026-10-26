"""The hospital confirms (or queries, or rejects) a bill. This is the gate before any money moves.

A confirmation needs a one-time code that is emailed only to the hospital's official contact in
the public facility register, never to an address from the document or to the staff member's own
login. Above the hospital's threshold a second, different supervisor must approve with a fresh
code. Each code authorises exactly one decision.
"""

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models.case import Case, CaseDocument, ReviewItem
from app.models.enums import (
    ActorType,
    CaseStatus,
    ConfirmationRequestStatus,
    ConfirmationStatus,
    ConsentScope,
    DocumentKind,
    DocumentStatus,
    ReviewSource,
    StaffRole,
)
from app.models.hospital import HospitalStaff
from app.models.verification import Confirmation, ConfirmationRequest
from app.security import crypto
from app.security.masking import mask_email
from app.services import (
    audit_service,
    case_service,
    fx_service,
    mail_service,
    notification_service,
)

logger = logging.getLogger(__name__)

OTP_DIGITS = 6
OTP_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
PURPOSES = ("decision", "second_approval")
MAX_BILL_KES_MINOR = 5_000_000_000  # KES 50 million: above this is almost surely a typing error
INVOICE_NUMBER_MAX = 60
NOTE_MAX = 500
_OTP_CONTEXT = "hospital_otp"

_PURPOSE_TEXT = {
    "decision": {"en": "confirm a bill", "sw": "kuthibitisha bili"},
    "second_approval": {
        "en": "give the second approval of a bill",
        "sw": "kutoa idhini ya pili ya bili",
    },
}


class OtpInvalid(AppError):
    status_code = 422
    code = "otp_invalid"
    message = "That code is not valid. Check it, or ask for a new one."


class OtpExpired(AppError):
    status_code = 422
    code = "otp_expired"
    message = "That code has expired. Ask for a new one."


class OtpLocked(AppError):
    status_code = 422
    code = "otp_locked"
    message = "Too many wrong tries. Ask for a new code."


class OtpNotSent(AppError):
    status_code = 503
    code = "otp_not_sent"
    message = "The code could not be emailed to the hospital's official address. Try again shortly."


@dataclass(frozen=True)
class IssuedOtp:
    """A sent code's request. ``code`` is only for tests; the API never returns it."""

    request: ConfirmationRequest
    code: str


def _now() -> datetime:
    return datetime.now(UTC)


def _otp_hmac(request_id: uuid.UUID, code: str) -> str:
    # Bound to the request, so a code from one request never opens another.
    return crypto.lookup_hash(f"{request_id}|{code}", context=_OTP_CONTEXT)


def current_bill(case: Case) -> CaseDocument | None:
    """The newest bill on the case: the document a confirmation refers to."""
    bills = [
        d
        for d in case.documents
        if d.kind in (DocumentKind.INVOICE, DocumentKind.ESTIMATE, DocumentKind.FINAL_BILL)
    ]
    return max(bills, key=lambda document: document.created_at, default=None)


# A final bill is decided while the case is already taking money.
FINAL_BILL_STATUSES = (CaseStatus.LIVE, CaseStatus.FUNDED)


def awaiting_final_bill(case: Case) -> CaseDocument | None:
    """The newest checked final bill the hospital has not decided on yet, on a live case."""
    if case.status not in FINAL_BILL_STATUSES:
        return None
    decided = {c.document_id for c in case.confirmations}
    waiting = [
        d
        for d in case.documents
        if d.kind == DocumentKind.FINAL_BILL
        and d.status == DocumentStatus.TRIAGED
        and d.id not in decided
    ]
    return max(waiting, key=lambda d: d.created_at, default=None)


def awaiting_decision(case: Case) -> bool:
    """True if the hospital has a bill to decide on: the first bill, or a later final bill."""
    return case.status == CaseStatus.PENDING_CONFIRMATION or awaiting_final_bill(case) is not None


def second_approval_possible(case: Case) -> bool:
    return case.status == CaseStatus.PENDING_CONFIRMATION or case.status in FINAL_BILL_STATUSES


def awaiting_second_approval(case: Case) -> Confirmation | None:
    return next(
        (c for c in case.confirmations if c.status == ConfirmationStatus.AWAITING_SECOND_APPROVAL),
        None,
    )


def confirmed(case: Case) -> Confirmation | None:
    """The newest confirmed bill, if any."""
    done = [c for c in case.confirmations if c.status == ConfirmationStatus.CONFIRMED]
    return max(done, key=lambda c: c.finalized_at or c.created_at, default=None)


def _require_supervisor_who_did_not_confirm(
    staff: HospitalStaff, confirmation: Confirmation
) -> None:
    if staff.staff_role != StaffRole.BILLING_SUPERVISOR:
        raise PermissionDenied(
            "A billing supervisor must give the second approval.", code="supervisor_required"
        )
    if staff.id == confirmation.confirmed_by_staff_id:
        raise PermissionDenied(
            "A different person must give the second approval.",
            code="same_person_cannot_approve",
        )


# --------------------------------------------------------------------------- one-time codes


def send_otp(session: Session, case: Case, staff: HospitalStaff, purpose: str) -> IssuedOtp:
    """Email a one-time code to the hospital's official contact. The caller commits."""
    if purpose not in PURPOSES:
        raise ValidationFailed(
            "Unknown purpose.", details={"json": {"purpose": ["Unknown purpose."]}}
        )
    pending = awaiting_second_approval(case)
    if purpose == "decision":
        if not awaiting_decision(case):
            raise Conflict(
                "This case is not waiting for the hospital's decision.",
                code="case_not_awaiting_confirmation",
            )
        if pending is not None:
            raise Conflict(
                "This bill is waiting for a second approval.", code="awaiting_second_approval"
            )
    else:
        if pending is None or not second_approval_possible(case):
            raise Conflict("No second approval is pending.", code="no_second_approval_pending")
        _require_supervisor_who_did_not_confirm(staff, pending)

    hospital = staff.hospital
    destination = hospital.official_email  # the register's contact, and no other source
    request_id = uuid.uuid4()
    code = f"{secrets.randbelow(10**OTP_DIGITS):0{OTP_DIGITS}d}"
    for older in session.execute(
        select(ConfirmationRequest).where(
            ConfirmationRequest.case_id == case.id,
            ConfirmationRequest.requested_by_staff_id == staff.id,
            ConfirmationRequest.purpose == purpose,
            ConfirmationRequest.status == ConfirmationRequestStatus.SENT,
        )
    ).scalars():
        older.status = ConfirmationRequestStatus.EXPIRED

    request = ConfirmationRequest(
        id=request_id,
        case=case,
        hospital_id=hospital.id,
        requested_by=staff,
        purpose=purpose,
        destination_masked=mask_email(destination),
        otp_hmac=_otp_hmac(request_id, code),
        expires_at=_now() + OTP_TTL,
    )
    session.add(request)
    session.flush()

    language = staff.user.preferred_language
    try:
        mail_service.send(
            "hospital_otp",
            destination,
            language=language,
            code=code,
            hospital=hospital.display_name,
            requested_by=staff.user.display_name,
            alias=case.alias,
            purpose_text=_PURPOSE_TEXT[purpose][mail_service.resolve_language(language)],
            minutes=int(OTP_TTL.total_seconds() // 60),
        )
    except mail_service.MailError as exc:
        logger.error("hospital code email failed", extra={"reason": str(exc)})
        request.status = ConfirmationRequestStatus.EXPIRED  # nobody holds this code
        session.flush()
        raise OtpNotSent() from exc

    audit_service.record(
        "confirmation.otp_sent",
        case,
        actor=staff.user,
        details={"purpose": purpose, "confirmation_request_id": request.id},
    )
    return IssuedOtp(request, code)


def _verify_otp(
    session: Session,
    request_id: uuid.UUID,
    code: str,
    *,
    case: Case,
    staff: HospitalStaff,
    purpose: str,
) -> ConfirmationRequest:
    """Check a code against its request. Wrong guesses are saved at once, even when this raises."""
    request = session.execute(
        select(ConfirmationRequest).where(ConfirmationRequest.id == request_id).with_for_update()
    ).scalar_one_or_none()
    if (
        request is None
        or request.case_id != case.id
        or request.requested_by_staff_id != staff.id
        or request.purpose != purpose
        or request.status == ConfirmationRequestStatus.VERIFIED
    ):
        raise OtpInvalid()
    if request.status == ConfirmationRequestStatus.LOCKED or request.attempts >= MAX_ATTEMPTS:
        raise OtpLocked()
    if request.status == ConfirmationRequestStatus.EXPIRED:
        raise OtpExpired()
    now = _now()
    if request.expires_at <= now:
        request.status = ConfirmationRequestStatus.EXPIRED
        session.commit()
        raise OtpExpired()
    if not crypto.hashes_match(request.otp_hmac, _otp_hmac(request.id, code.strip())):
        request.attempts += 1
        if request.attempts >= MAX_ATTEMPTS:
            request.status = ConfirmationRequestStatus.LOCKED
        session.commit()
        raise OtpLocked() if request.status == ConfirmationRequestStatus.LOCKED else OtpInvalid()
    request.status = ConfirmationRequestStatus.VERIFIED
    request.verified_at = now
    return request


# --------------------------------------------------------------------------- decisions


def _amount_band(hospital_threshold: int, amount: int) -> str:
    return "above_threshold" if amount >= hospital_threshold else "below_threshold"


def _invoice_already_used(session: Session, case: Case, invoice_number: str) -> bool:
    return (
        session.execute(
            select(Confirmation.id)
            .where(
                Confirmation.hospital_id == case.hospital_id,
                Confirmation.case_id != case.id,
                Confirmation.status.in_(
                    [ConfirmationStatus.CONFIRMED, ConfirmationStatus.AWAITING_SECOND_APPROVAL]
                ),
                Confirmation.invoice_number.ilike(invoice_number),
            )
            .limit(1)
        ).first()
        is not None
    )


def decide(
    session: Session,
    case: Case,
    staff: HospitalStaff,
    *,
    request_id: uuid.UUID,
    otp: str,
    decision: ConfirmationStatus,
    amount_kes_minor: int | None = None,
    invoice_number: str | None = None,
    is_final_bill: bool = False,
    deposit_kes_minor: int | None = None,
    note: str | None = None,
) -> Confirmation:
    """Record the hospital's decision on the bill. The caller commits.

    ``decision`` is ``CONFIRMED`` (which may wait for a second approval), ``QUERIED`` or
    ``REJECTED``. Checks that can fail on the form run after the code is verified, but they run
    in the same transaction, so a refused decision leaves the code unused.
    """
    if not awaiting_decision(case):
        raise Conflict(
            "This case is not waiting for the hospital's decision.",
            code="case_not_awaiting_confirmation",
        )
    if awaiting_second_approval(case) is not None:
        raise Conflict(
            "This bill is waiting for a second approval.", code="awaiting_second_approval"
        )
    final_path = case.status in FINAL_BILL_STATUSES
    if final_path:
        # The case is taking money; changing its target must not interleave with a donation.
        case = case_service.lock_case(session, case.id)
    document = awaiting_final_bill(case) if final_path else current_bill(case)
    if document is None:
        raise Conflict("This case has no bill to decide on.", code="bill_required")
    if final_path:
        is_final_bill = True  # the document is a final bill, whatever the form says
    request = _verify_otp(session, request_id, otp, case=case, staff=staff, purpose="decision")
    note = (note or "").strip()[:NOTE_MAX] or None

    if decision == ConfirmationStatus.CONFIRMED:
        confirmation = _record_confirmation(
            session, case, staff, document, request,
            amount_kes_minor, invoice_number, is_final_bill, deposit_kes_minor,
        )  # fmt: skip
    else:
        confirmation = Confirmation(
            case=case,
            hospital_id=case.hospital_id,
            document=document,
            confirmation_request=request,
            confirmed_by=staff,
            status=decision,
            query_note=note,
        )
        session.add(confirmation)
        session.flush()
        if final_path:
            _handle_final_bill_not_confirmed(session, case, staff, confirmation)
        elif decision == ConfirmationStatus.QUERIED:
            _handle_query(session, case, staff, confirmation)
        else:
            _handle_rejection(case, staff, confirmation)
    return confirmation


def _record_confirmation(
    session: Session,
    case: Case,
    staff: HospitalStaff,
    document: CaseDocument,
    request: ConfirmationRequest,
    amount_kes_minor: int | None,
    invoice_number: str | None,
    is_final_bill: bool,
    deposit_kes_minor: int | None,
) -> Confirmation:
    errors: dict[str, list[str]] = {}
    invoice = " ".join((invoice_number or "").split())
    if amount_kes_minor is None or amount_kes_minor <= 0:
        errors["amount_kes"] = ["Enter the total on the bill."]
    elif amount_kes_minor > MAX_BILL_KES_MINOR:
        errors["amount_kes"] = ["That amount is too large. Check it."]
    if not invoice or len(invoice) > INVOICE_NUMBER_MAX:
        errors["invoice_number"] = ["Enter the invoice number from the bill."]
    if deposit_kes_minor is not None and (
        deposit_kes_minor < 0
        or (amount_kes_minor is not None and deposit_kes_minor > amount_kes_minor)
    ):
        errors["deposit_kes"] = ["The deposit cannot be more than the bill."]
    if errors:
        raise ValidationFailed(details={"json": errors}, code="confirmation_invalid")
    assert amount_kes_minor is not None
    if _invoice_already_used(session, case, invoice):
        raise Conflict(
            "That invoice number is already used on another case at this hospital.",
            code="invoice_already_used",
        )

    threshold = staff.hospital.two_person_threshold_kes_minor
    needs_second = amount_kes_minor >= threshold
    confirmation = Confirmation(
        case=case,
        hospital_id=case.hospital_id,
        document=document,
        confirmation_request=request,
        confirmed_by=staff,
        status=(
            ConfirmationStatus.AWAITING_SECOND_APPROVAL
            if needs_second
            else ConfirmationStatus.CONFIRMED
        ),
        amount_kes_minor=amount_kes_minor,
        invoice_number=invoice,
        is_final_bill=is_final_bill,
        deposit_kes_minor=deposit_kes_minor,
        finalized_at=None if needs_second else _now(),
    )
    session.add(confirmation)
    session.flush()
    audit_service.record(
        "confirmation.recorded",
        case,
        actor=staff.user,
        details={
            "confirmation_id": confirmation.id,
            "status": confirmation.status,
            "amount_band": _amount_band(threshold, amount_kes_minor),
            "is_final_bill": is_final_bill,
        },
    )
    if not needs_second:
        apply_confirmed(session, case, confirmation)
    return confirmation


def approve_second(
    session: Session,
    confirmation: Confirmation,
    staff: HospitalStaff,
    *,
    request_id: uuid.UUID,
    otp: str,
) -> Confirmation:
    """A different supervisor approves a bill above the threshold, with a fresh code."""
    if confirmation.hospital_id != staff.hospital_id:
        raise NotFound("Confirmation not found.", code="confirmation_not_found")
    if confirmation.status != ConfirmationStatus.AWAITING_SECOND_APPROVAL:
        raise Conflict(
            "This bill is not waiting for a second approval.", code="no_second_approval_pending"
        )
    case = confirmation.case
    if not second_approval_possible(case):
        raise Conflict(
            "This case is no longer waiting for confirmation.",
            code="case_not_awaiting_confirmation",
        )
    if case.status in FINAL_BILL_STATUSES:
        case = case_service.lock_case(session, case.id)
    _require_supervisor_who_did_not_confirm(staff, confirmation)
    request = _verify_otp(
        session, request_id, otp, case=case, staff=staff, purpose="second_approval"
    )
    confirmation.second_approver = staff
    confirmation.second_request = request
    confirmation.status = ConfirmationStatus.CONFIRMED
    confirmation.finalized_at = _now()
    session.flush()
    audit_service.record(
        "confirmation.second_approved",
        case,
        actor=staff.user,
        details={"confirmation_id": confirmation.id},
    )
    apply_confirmed(session, case, confirmation)
    return confirmation


def apply_confirmed(session: Session, case: Case, confirmation: Confirmation) -> None:
    """A bill is confirmed: set the target and USD goal, and go live if the page may be public."""
    case.target_kes_minor = confirmation.amount_kes_minor
    case.is_estimate = not confirmation.is_final_bill
    case.usd_goal_minor = fx_service.usd_goal_for(session, case)
    session.flush()
    audit_service.record(
        "confirmation.confirmed",
        case,
        actor=confirmation.confirmed_by.user,
        details={
            "confirmation_id": confirmation.id,
            "is_final_bill": confirmation.is_final_bill,
            "two_person": confirmation.second_approver_staff_id is not None,
        },
    )
    go_live_if_ready(session, case)
    if case.status in FINAL_BILL_STATUSES:
        _after_bill_on_live_case(session, case, confirmation)


def _after_bill_on_live_case(session: Session, case: Case, confirmation: Confirmation) -> None:
    """A bill was confirmed on a case already taking money: re-check funding and closure.

    A larger bill can reopen a funded case; a smaller one can fund it. If the hospital has
    already been paid more than a final bill asks for, that extra sits with the hospital as
    patient credit, which only a person can arrange, so an admin is asked to look.
    """
    case_service.sync_funding(case)
    if (
        confirmation.is_final_bill
        and case.target_kes_minor is not None
        and case.settled_kes_minor > case.target_kes_minor
    ):
        session.add(
            ReviewItem(
                case=case,
                source=ReviewSource.ADMIN,
                reason="The final bill is lower than what has already been paid to the hospital",
                payload={
                    "origin": "final_bill_below_settled",
                    "confirmation_id": str(confirmation.id),
                    "settled_kes_minor": case.settled_kes_minor,
                    "target_kes_minor": case.target_kes_minor,
                },
            )
        )
        session.flush()
    case_service.close_if_fully_settled(case)


def go_live_if_ready(session: Session, case: Case) -> bool:
    """Make a confirmed case live once the family's public-page consent is in place.

    Called when a bill is confirmed and again when the family grants the public-page consent,
    in case it was missing at the time. Returns True if the case went live.
    """
    if case.status != CaseStatus.PENDING_CONFIRMATION or confirmed(case) is None:
        return False
    if case.active_consent(ConsentScope.PUBLIC_PAGE) is None:
        return False
    case_service.transition(case, CaseStatus.LIVE, ActorType.SYSTEM, "hospital_confirmed")
    notification_service.notify_family(
        case,
        "case_live",
        url=f"{current_app.config['APP_BASE_URL'].rstrip('/')}/c/{case.public_slug}",
    )
    return True


def _handle_query(
    session: Session, case: Case, staff: HospitalStaff, confirmation: Confirmation
) -> None:
    case_service.transition(case, CaseStatus.IN_REVIEW, staff.user, "hospital_query")
    reason = "The hospital has a question about the bill"
    if confirmation.query_note:
        reason = f"{reason}: {confirmation.query_note}"
    session.add(
        ReviewItem(
            case=case,
            source=ReviewSource.ADMIN,
            reason=reason[:300],
            payload={"origin": "hospital_query", "confirmation_id": str(confirmation.id)},
        )
    )
    session.flush()
    audit_service.record(
        "confirmation.queried",
        case,
        actor=staff.user,
        details={"confirmation_id": confirmation.id},
    )
    notification_service.notify_family(case, "case_queried")


def _handle_final_bill_not_confirmed(
    session: Session, case: Case, staff: HospitalStaff, confirmation: Confirmation
) -> None:
    """The hospital queried or rejected a final bill. The case stays live on its estimate; an
    admin is asked to look and the family can upload another final bill."""
    queried = confirmation.status == ConfirmationStatus.QUERIED
    reason = (
        "The hospital has a question about the final bill"
        if queried
        else "The hospital did not confirm the final bill"
    )
    if confirmation.query_note:
        reason = f"{reason}: {confirmation.query_note}"
    session.add(
        ReviewItem(
            case=case,
            source=ReviewSource.ADMIN,
            reason=reason[:300],
            payload={
                "origin": "final_bill_query" if queried else "final_bill_rejected",
                "confirmation_id": str(confirmation.id),
            },
        )
    )
    session.flush()
    audit_service.record(
        "confirmation.queried" if queried else "confirmation.rejected",
        case,
        actor=staff.user,
        details={"confirmation_id": confirmation.id, "is_final_bill": True},
    )
    notification_service.notify_family(case, "case_queried" if queried else "case_rejected")


def _handle_rejection(case: Case, staff: HospitalStaff, confirmation: Confirmation) -> None:
    case_service.transition(case, CaseStatus.CANCELLED, staff.user, "hospital_rejected")
    audit_service.record(
        "confirmation.rejected",
        case,
        actor=staff.user,
        details={"confirmation_id": confirmation.id},
    )
    notification_service.notify_family(case, "case_rejected")
