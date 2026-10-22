"""Schemas for cases: what a family sends, and the three views of a case.

The views are whitelists. A marshmallow schema dumps only the fields declared on it, so a field
added to the model later cannot leak into a view by accident. The public view in particular
never carries the patient reference, documents, triage details or anyone's identity.
"""

import marshmallow as ma
from sqlalchemy.orm import object_session

from app.money import format_amount
from app.services import case_service


def _money(minor_attribute: str) -> ma.fields.Function:
    """A formatted decimal string ("1250.00") for an amount held in minor units."""
    return ma.fields.Function(
        lambda case: (
            None
            if getattr(case, minor_attribute) is None
            else format_amount(getattr(case, minor_attribute))
        )
    )


# --- What a family sends ------------------------------------------------------------------------


class CreateCaseSchema(ma.Schema):
    hospital_id = ma.fields.UUID(required=True)
    alias = ma.fields.String(required=True, validate=ma.validate.Length(max=120))
    region = ma.fields.String(required=True, validate=ma.validate.Length(max=120))
    patient_reference = ma.fields.String(required=True, validate=ma.validate.Length(max=200))
    is_minor = ma.fields.Boolean(load_default=False)


class UpdateCaseSchema(ma.Schema):
    alias = ma.fields.String(validate=ma.validate.Length(max=120))
    region = ma.fields.String(validate=ma.validate.Length(max=120))
    story = ma.fields.String(allow_none=True, validate=ma.validate.Length(max=2000))
    patient_reference = ma.fields.String(validate=ma.validate.Length(max=200))
    is_minor = ma.fields.Boolean()
    show_photo = ma.fields.Boolean()


class PublicCasesQuerySchema(ma.Schema):
    class Meta:
        unknown = ma.EXCLUDE

    county = ma.fields.String(load_default=None, validate=ma.validate.Length(max=40))


# --- Shared pieces ------------------------------------------------------------------------------


class HospitalNameSchema(ma.Schema):
    id = ma.fields.UUID()
    display_name = ma.fields.String()
    county = ma.fields.String()


class TimelineEntrySchema(ma.Schema):
    status = ma.fields.String()
    at = ma.fields.DateTime()


class MoneySummarySchema(ma.Schema):
    target_kes_minor = ma.fields.Integer(allow_none=True)
    target_kes = _money("target_kes_minor")
    is_estimate = ma.fields.Boolean()
    usd_goal_minor = ma.fields.Integer(allow_none=True)
    usd_goal = _money("usd_goal_minor")
    pledged_kes_minor = ma.fields.Integer()
    pledged_kes = _money("pledged_kes_minor")
    settled_kes_minor = ma.fields.Integer()
    settled_kes = _money("settled_kes_minor")
    captured_net_usd_minor = ma.fields.Integer()
    captured_net_usd = _money("captured_net_usd_minor")
    refunded_usd_minor = ma.fields.Integer()
    refunded_usd = _money("refunded_usd_minor")
    donor_count = ma.fields.Integer()


class ConfirmationSummarySchema(ma.Schema):
    status = ma.fields.Function(lambda c: c.status.value)
    amount_kes_minor = ma.fields.Integer(allow_none=True)
    invoice_number = ma.fields.String(allow_none=True)
    is_final_bill = ma.fields.Boolean()
    query_note = ma.fields.String(
        allow_none=True,
        metadata={"description": "The hospital's message when it queried or rejected"},
    )
    created_at = ma.fields.DateTime()
    finalized_at = ma.fields.DateTime(allow_none=True)


class TriageSummarySchema(ma.Schema):
    risk_band = ma.fields.Function(lambda t: t.risk_band.value)
    route = ma.fields.Function(lambda t: t.route.value)
    reasons_en = ma.fields.List(ma.fields.String())
    reasons_sw = ma.fields.List(ma.fields.String())
    created_at = ma.fields.DateTime()


# --- The three views of a case ------------------------------------------------------------------


class FamilyCaseListItemSchema(ma.Schema):
    """A row in the family's own list of cases."""

    id = ma.fields.UUID()
    public_slug = ma.fields.String()
    alias = ma.fields.String()
    region = ma.fields.String()
    status = ma.fields.Function(lambda case: case.status.value)
    trust_level = ma.fields.Function(lambda case: case.trust_level.value)
    hospital = ma.fields.Nested(HospitalNameSchema)
    is_publicly_visible = ma.fields.Boolean()
    money = ma.fields.Function(lambda case: MoneySummarySchema().dump(case))
    created_at = ma.fields.DateTime()


class FamilyCaseSchema(FamilyCaseListItemSchema):
    """The family's own full view, including what only they and the hospital may see."""

    story = ma.fields.String(allow_none=True)
    patient_reference = ma.fields.String()
    is_minor = ma.fields.Boolean()
    show_photo = ma.fields.Boolean()
    consents = ma.fields.Function(
        lambda case: sorted(
            consent.scope.value for consent in case.consents if consent.withdrawn_at is None
        )
    )
    timeline = ma.fields.Function(
        lambda case: TimelineEntrySchema(many=True).dump(
            case_service.status_timeline(object_session(case), case)
        )
    )
    triage = ma.fields.Function(
        lambda case: (
            TriageSummarySchema().dump(triage)
            if (triage := case_service.latest_triage(case))
            else None
        )
    )
    confirmations = ma.fields.Function(
        lambda case: ConfirmationSummarySchema(many=True).dump(
            case_service.confirmations_newest_first(case)
        )
    )
    is_accepting_donations = ma.fields.Function(case_service.is_accepting_donations)
    submitted_at = ma.fields.DateTime(allow_none=True)
    live_at = ma.fields.DateTime(allow_none=True)
    closed_at = ma.fields.DateTime(allow_none=True)
    updated_at = ma.fields.DateTime()


class HospitalCaseSchema(ma.Schema):
    """What the case's own hospital sees: the case and patient reference, never the family."""

    id = ma.fields.UUID()
    alias = ma.fields.String()
    region = ma.fields.String()
    patient_reference = ma.fields.String()
    status = ma.fields.Function(lambda case: case.status.value)
    trust_level = ma.fields.Function(lambda case: case.trust_level.value)
    target_kes_minor = ma.fields.Integer(allow_none=True)
    is_estimate = ma.fields.Boolean()
    settled_kes_minor = ma.fields.Integer()
    submitted_at = ma.fields.DateTime(allow_none=True)
    live_at = ma.fields.DateTime(allow_none=True)


class PublicCaseSchema(ma.Schema):
    """The page anyone may read. Only fields named here ever leave the server."""

    slug = ma.fields.String(attribute="public_slug")
    alias = ma.fields.String()
    region = ma.fields.String()
    hospital_name = ma.fields.Function(lambda case: case.hospital.display_name)
    trust_level = ma.fields.Function(lambda case: case.trust_level.value)
    status = ma.fields.Function(lambda case: case.status.value)
    story = ma.fields.String(allow_none=True)
    photo_url = ma.fields.Function(lambda case: None)  # photos arrive with the P1 photo feature
    is_estimate = ma.fields.Boolean()
    target_kes_minor = ma.fields.Integer(allow_none=True)
    target_kes = _money("target_kes_minor")
    pledged_kes_minor = ma.fields.Integer()
    pledged_kes = _money("pledged_kes_minor")
    settled_kes_minor = ma.fields.Integer()
    settled_kes = _money("settled_kes_minor")
    usd_goal_minor = ma.fields.Integer(allow_none=True)
    usd_goal = _money("usd_goal_minor")
    donor_count = ma.fields.Integer()
    created_at = ma.fields.DateTime()
    live_at = ma.fields.DateTime(allow_none=True)


class CancelCaseSchema(ma.Schema):
    reason = ma.fields.String(
        required=True,
        validate=ma.validate.OneOf(["treatment_cancelled", "patient_deceased", "other"]),
        metadata={"description": "Why the case is ending"},
    )
