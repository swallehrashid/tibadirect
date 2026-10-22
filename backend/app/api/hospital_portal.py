"""The hospital portal: confirm a bill with a one-time code, and see what was paid."""

import uuid

from flask import request
from flask.views import MethodView
from sqlalchemy import and_, exists, or_, select

from app.api.base import TibaBlueprint
from app.errors import NotFound
from app.extensions import db, limiter
from app.models.case import Case, CaseDocument
from app.models.enums import CaseStatus, ConfirmationStatus, DocumentKind, DocumentStatus, UserRole
from app.models.money import Settlement
from app.models.verification import Confirmation
from app.money import to_minor
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.schemas.hospital_portal import (
    ApproveSchema,
    DecisionResultSchema,
    DecisionSchema,
    HospitalCaseDetailSchema,
    OtpRequestSchema,
    OtpSentSchema,
    QueueSchema,
    SettlementItemSchema,
)
from app.security.permissions import current_staff, get_hospital_case_or_404
from app.services import (
    case_service,
    confirmation_service,
    document_service,
    excess_policy_service,
    invoicing_service,
    notification_service,
    settlement_service,
)

blp = TibaBlueprint(
    "hospital_portal", __name__, url_prefix="/api/hospital", description="Hospital portal"
)


def _per_case() -> str:
    """Rate-limit bucket for sending codes: one case, whoever asks."""
    return f"case:{(request.view_args or {}).get('case_id')}"


@blp.route("/queue")
class Queue(MethodView):
    @blp.secured(UserRole.HOSPITAL_STAFF)
    @blp.response(200, QueueSchema)
    def get(self) -> dict:
        """Bills waiting for the hospital's decision, and bills waiting for a second approval.

        A bill is either a case's first bill or a final bill added to a case that is live.
        """
        staff = current_staff()
        decidable = (
            Case.status == CaseStatus.PENDING_CONFIRMATION,
            and_(
                Case.status.in_(confirmation_service.FINAL_BILL_STATUSES),
                exists().where(
                    CaseDocument.case_id == Case.id,
                    CaseDocument.kind == DocumentKind.FINAL_BILL,
                    CaseDocument.status == DocumentStatus.TRIAGED,
                    ~exists().where(Confirmation.document_id == CaseDocument.id),
                ),
            ),
        )
        cases = db.session.execute(
            select(Case)
            .where(Case.hospital_id == staff.hospital_id, or_(*decidable))
            .order_by(Case.submitted_at, Case.id)
        ).scalars()
        waiting_second = list(
            db.session.execute(
                select(Confirmation)
                .join(Case, Confirmation.case_id == Case.id)
                .where(
                    Confirmation.hospital_id == staff.hospital_id,
                    Confirmation.status == ConfirmationStatus.AWAITING_SECOND_APPROVAL,
                    Case.status.in_(
                        [CaseStatus.PENDING_CONFIRMATION, *confirmation_service.FINAL_BILL_STATUSES]
                    ),
                )
                .order_by(Confirmation.created_at)
            ).scalars()
        )
        awaiting_case_ids = {c.case_id for c in waiting_second}
        pending = []
        for case in cases:
            if case.id in awaiting_case_ids:
                continue
            triage = case_service.latest_triage(case)
            pending.append(
                {
                    "id": case.id,
                    "alias": case.alias,
                    "region": case.region,
                    "submitted_at": case.submitted_at,
                    "risk_band": triage.risk_band.value if triage else None,
                    "bills": len(document_service.list_documents(case)),
                    "kind": "final_bill"
                    if case.status != CaseStatus.PENDING_CONFIRMATION
                    else "first_bill",
                }
            )
        return {"pending": pending, "awaiting_second_approval": waiting_second}


@blp.route("/cases/<uuid:case_id>")
class CaseDetail(MethodView):
    @blp.secured(UserRole.HOSPITAL_STAFF)
    @blp.response(200, HospitalCaseDetailSchema)
    def get(self, case_id: uuid.UUID) -> Case:
        """Bill details: the original while it exists, what the checks found and the history."""
        return get_hospital_case_or_404(case_id, current_staff())


@blp.route("/cases/<uuid:case_id>/otp")
class SendOtp(MethodView):
    @blp.secured(UserRole.HOSPITAL_STAFF)
    @limiter.limit("5 per hour", key_func=_per_case, scope="hospital_otp")
    @blp.arguments(OtpRequestSchema)
    @blp.response(201, OtpSentSchema)
    @blp.alt_response(409, description="The case is not waiting for that step")
    @blp.alt_response(503, description="The email could not be sent")
    def post(self, payload: dict, case_id: uuid.UUID) -> object:
        """Email a one-time code to the hospital's official address from the facility register."""
        staff = current_staff()
        case = get_hospital_case_or_404(case_id, staff)
        issued = confirmation_service.send_otp(db.session, case, staff, payload["purpose"])
        db.session.commit()
        request = issued.request
        return {
            "request_id": request.id,
            "destination_masked": request.destination_masked,
            "expires_at": request.expires_at,
        }


def _after_decision(confirmation: Confirmation, status_before: CaseStatus) -> None:
    """After a confirmed bill is saved: a larger or final bill may release money that was
    waiting, and a final bill smaller than what is already paid closes the case."""
    if confirmation.status != ConfirmationStatus.CONFIRMED:
        return
    case = confirmation.case
    invoicing_service.enqueue_issue(confirmation)
    settlement_service.enqueue_check(case.id)
    if status_before != CaseStatus.CLOSED and case.status == CaseStatus.CLOSED:
        notification_service.notify_closed(case)
        excess_policy_service.enqueue_run(case.id)


@blp.route("/cases/<uuid:case_id>/decision")
class Decision(MethodView):
    @blp.secured(UserRole.HOSPITAL_STAFF)
    @blp.arguments(DecisionSchema)
    @blp.response(200, DecisionResultSchema)
    @blp.alt_response(409, description="Not waiting for a decision, or the invoice number is used")
    @blp.alt_response(422, description="Wrong or expired code, or invalid amounts")
    def post(self, payload: dict, case_id: uuid.UUID) -> dict:
        """Confirm, query or reject a bill, with the one-time code from the register address."""
        staff = current_staff()
        case = get_hospital_case_or_404(case_id, staff)
        status_before = case.status
        amount = payload["amount_kes"]
        deposit = payload["deposit_kes"]
        confirmation = confirmation_service.decide(
            db.session,
            case,
            staff,
            request_id=payload["request_id"],
            otp=payload["otp"],
            decision=payload["decision"],
            amount_kes_minor=to_minor(amount) if amount is not None else None,
            invoice_number=payload["invoice_number"],
            is_final_bill=payload["is_final_bill"],
            deposit_kes_minor=to_minor(deposit) if deposit is not None else None,
            note=payload["note"],
        )
        db.session.commit()
        _after_decision(confirmation, status_before)
        return {"confirmation": confirmation, "case_status": case.status.value}


@blp.route("/confirmations/<uuid:confirmation_id>/approve")
class Approve(MethodView):
    @blp.secured(UserRole.HOSPITAL_STAFF)
    @blp.arguments(ApproveSchema)
    @blp.response(200, DecisionResultSchema)
    @blp.alt_response(403, description="Not a supervisor, or the same person who confirmed")
    @blp.alt_response(404, description="No such confirmation at this hospital")
    def post(self, payload: dict, confirmation_id: uuid.UUID) -> dict:
        """A different supervisor gives the second approval, with a fresh code."""
        staff = current_staff()
        confirmation = db.session.get(Confirmation, confirmation_id)
        if confirmation is None or confirmation.hospital_id != staff.hospital_id:
            raise NotFound("Confirmation not found.", code="confirmation_not_found")
        status_before = confirmation.case.status
        confirmation_service.approve_second(
            db.session,
            confirmation,
            staff,
            request_id=payload["request_id"],
            otp=payload["otp"],
        )
        db.session.commit()
        _after_decision(confirmation, status_before)
        return {"confirmation": confirmation, "case_status": confirmation.case.status.value}


@blp.route("/settlements")
class Settlements(MethodView):
    @blp.secured(UserRole.HOSPITAL_STAFF)
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(SettlementItemSchema))
    def get(self, paging: dict) -> dict:
        """Payments this hospital has received, newest first."""
        statement = (
            select(Settlement)
            .join(Case, Settlement.case_id == Case.id)
            .where(Case.hospital_id == current_staff().hospital_id)
            .order_by(Settlement.requested_at.desc(), Settlement.id)
        )
        return paginate(db.session, statement, paging)
