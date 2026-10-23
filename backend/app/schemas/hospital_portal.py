"""Schemas for the hospital portal."""

import marshmallow as ma

from app.models.enums import ConfirmationStatus
from app.money import format_amount
from app.security.masking import mask_email
from app.services import case_service, confirmation_service, document_service


def _kes(attribute: str) -> ma.fields.Function:
    return ma.fields.Function(
        lambda obj: (
            None if getattr(obj, attribute) is None else format_amount(getattr(obj, attribute))
        )
    )


class OtpRequestSchema(ma.Schema):
    purpose = ma.fields.String(
        required=True, validate=ma.validate.OneOf(list(confirmation_service.PURPOSES))
    )


class OtpSentSchema(ma.Schema):
    request_id = ma.fields.UUID()
    destination_masked = ma.fields.String(
        metadata={"description": "The register's official address, masked. Codes go nowhere else."}
    )
    expires_at = ma.fields.DateTime()


class DecisionSchema(ma.Schema):
    request_id = ma.fields.UUID(required=True)
    otp = ma.fields.String(required=True, validate=ma.validate.Regexp(r"^\s*\d{6}\s*$"))
    decision = ma.fields.Enum(
        ConfirmationStatus,
        by_value=True,
        required=True,
        validate=ma.validate.OneOf(
            [ConfirmationStatus.CONFIRMED, ConfirmationStatus.QUERIED, ConfirmationStatus.REJECTED]
        ),
        metadata={"description": "confirmed, queried or rejected"},
    )
    amount_kes = ma.fields.Decimal(places=2, allow_none=True, load_default=None)
    invoice_number = ma.fields.String(allow_none=True, load_default=None)
    is_final_bill = ma.fields.Boolean(load_default=False)
    deposit_kes = ma.fields.Decimal(places=2, allow_none=True, load_default=None)
    note = ma.fields.String(
        allow_none=True,
        load_default=None,
        validate=ma.validate.Length(max=confirmation_service.NOTE_MAX),
        metadata={"description": "Shown to the family. Do not include medical details."},
    )


class ApproveSchema(ma.Schema):
    request_id = ma.fields.UUID(required=True)
    otp = ma.fields.String(required=True, validate=ma.validate.Regexp(r"^\s*\d{6}\s*$"))


class ConfirmationSchema(ma.Schema):
    id = ma.fields.UUID()
    status = ma.fields.Function(lambda c: c.status.value)
    amount_kes_minor = ma.fields.Integer(allow_none=True)
    amount_kes = _kes("amount_kes_minor")
    invoice_number = ma.fields.String(allow_none=True)
    is_final_bill = ma.fields.Boolean()
    deposit_kes_minor = ma.fields.Integer(allow_none=True)
    confirmed_by = ma.fields.Function(lambda c: c.confirmed_by.user.display_name)
    second_approver = ma.fields.Function(
        lambda c: c.second_approver.user.display_name if c.second_approver else None
    )
    query_note = ma.fields.String(allow_none=True)
    paypal_invoice_id = ma.fields.String(
        allow_none=True, metadata={"description": "The PayPal invoice issued for this bill, if any"}
    )
    invoice_status = ma.fields.String(
        allow_none=True,
        metadata={
            "description": "PayPal's status for that invoice: DRAFT, SENT, PARTIALLY_PAID, PAID, "
            "CANCELLED, or FAILED if it could not be issued"
        },
    )
    created_at = ma.fields.DateTime()
    finalized_at = ma.fields.DateTime(allow_none=True)


class DecisionResultSchema(ma.Schema):
    confirmation = ma.fields.Nested(ConfirmationSchema)
    case_status = ma.fields.String()


class QueueCaseSchema(ma.Schema):
    id = ma.fields.UUID()
    alias = ma.fields.String()
    region = ma.fields.String()
    submitted_at = ma.fields.DateTime(allow_none=True)
    risk_band = ma.fields.String(allow_none=True)
    bills = ma.fields.Integer()
    kind = ma.fields.String(
        metadata={"description": "first_bill, or final_bill for a live case's final bill"}
    )


class QueueConfirmationSchema(ConfirmationSchema):
    """A confirmation waiting for a second approval, with its case so a supervisor can open it."""

    case_id = ma.fields.Function(lambda c: str(c.case_id))
    case_alias = ma.fields.Function(lambda c: c.case.alias)


class QueueSchema(ma.Schema):
    pending = ma.fields.List(ma.fields.Nested(QueueCaseSchema))
    awaiting_second_approval = ma.fields.List(ma.fields.Nested(QueueConfirmationSchema))


class BillSchema(ma.Schema):
    id = ma.fields.UUID()
    kind = ma.fields.Function(lambda d: d.kind.value)
    mime_type = ma.fields.String()
    page_count = ma.fields.Integer(allow_none=True)
    created_at = ma.fields.DateTime()
    preview_url = ma.fields.Function(
        document_service.preview_url,
        metadata={"description": "A five-minute link to the original while it still exists"},
    )


class TriageForHospitalSchema(ma.Schema):
    risk_band = ma.fields.Function(lambda t: t.risk_band.value)
    reasons_en = ma.fields.List(ma.fields.String())
    reasons_sw = ma.fields.List(ma.fields.String())
    extracted = ma.fields.Dict()
    created_at = ma.fields.DateTime()


class HospitalCaseDetailSchema(ma.Schema):
    id = ma.fields.UUID()
    alias = ma.fields.String()
    region = ma.fields.String()
    patient_reference = ma.fields.String()
    status = ma.fields.Function(lambda case: case.status.value)
    submitted_at = ma.fields.DateTime(allow_none=True)
    bills = ma.fields.Function(
        lambda case: BillSchema(many=True).dump(
            sorted(
                (d for d in case.documents if d.kind.value != "photo"),
                key=lambda d: d.created_at,
            )
        )
    )
    triage = ma.fields.Function(
        lambda case: (
            TriageForHospitalSchema().dump(result)
            if (result := case_service.latest_triage(case)) is not None
            else None
        )
    )
    confirmations = ma.fields.Function(
        lambda case: ConfirmationSchema(many=True).dump(
            sorted(case.confirmations, key=lambda c: c.created_at, reverse=True)
        )
    )
    two_person_threshold_kes_minor = ma.fields.Function(
        lambda case: case.hospital.two_person_threshold_kes_minor
    )
    otp_destination_masked = ma.fields.Function(
        lambda case: mask_email(case.hospital.official_email)
    )


class SettlementItemSchema(ma.Schema):
    id = ma.fields.UUID()
    case_alias = ma.fields.Function(lambda s: s.case.alias)
    tranche_no = ma.fields.Integer()
    status = ma.fields.Function(lambda s: s.status.value)
    amount_kes_minor = ma.fields.Integer()
    amount_kes = _kes("amount_kes_minor")
    external_reference = ma.fields.Function(lambda s: s.external_batch_id)
    requested_at = ma.fields.DateTime()
    completed_at = ma.fields.DateTime(allow_none=True)
