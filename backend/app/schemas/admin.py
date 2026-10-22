"""Schemas for the admin endpoints."""

import marshmallow as ma
from sqlalchemy.orm import object_session

from app.models.enums import CaseStatus, ReviewResolution, ReviewStatus
from app.schemas.cases import MoneySummarySchema
from app.schemas.documents import DocumentSchema
from app.schemas.hospital_portal import ConfirmationSchema
from app.services import admin_service, case_service, consent_service, document_service


class QueueStatsSchema(ma.Schema):
    name = ma.fields.String()
    queued = ma.fields.Integer()
    started = ma.fields.Integer()
    scheduled = ma.fields.Integer()
    failed = ma.fields.Integer()


class FailedJobSchema(ma.Schema):
    id = ma.fields.String()
    queue = ma.fields.String()
    function = ma.fields.String()
    enqueued_at = ma.fields.DateTime(allow_none=True)
    failed_at = ma.fields.DateTime(allow_none=True)
    retries_left = ma.fields.Integer()
    request_id = ma.fields.String(allow_none=True)
    error = ma.fields.String(
        metadata={"description": "Last line of the error, personal data removed"}
    )


class SchedulerStatusSchema(ma.Schema):
    last_heartbeat = ma.fields.DateTime(allow_none=True)
    age_seconds = ma.fields.Integer(allow_none=True)
    healthy = ma.fields.Boolean()


class JobsOverviewSchema(ma.Schema):
    queues = ma.fields.List(ma.fields.Nested(QueueStatsSchema))
    failed_jobs = ma.fields.List(ma.fields.Nested(FailedJobSchema))
    scheduler = ma.fields.Nested(SchedulerStatusSchema)


class RetriedJobSchema(ma.Schema):
    id = ma.fields.String()
    queue = ma.fields.String()
    status = ma.fields.String()


# --- Stats ---------------------------------------------------------------------------------------


class MoneyTotalsSchema(ma.Schema):
    captured_net_usd_minor = ma.fields.Integer()
    refunded_usd_minor = ma.fields.Integer()
    settled_usd_minor = ma.fields.Integer()
    pledged_kes_minor = ma.fields.Integer()
    settled_kes_minor = ma.fields.Integer()
    donations = ma.fields.Integer()


class WaitingSchema(ma.Schema):
    open_review_items = ma.fields.Integer()
    awaiting_second_approval = ma.fields.Integer()


class AdminStatsSchema(ma.Schema):
    cases_by_status = ma.fields.Dict(keys=ma.fields.String(), values=ma.fields.Integer())
    cases_total = ma.fields.Integer()
    money = ma.fields.Nested(MoneyTotalsSchema)
    waiting = ma.fields.Nested(WaitingSchema)
    users = ma.fields.Dict(keys=ma.fields.String(), values=ma.fields.Integer())
    ledger_entries = ma.fields.Integer()
    queues = ma.fields.List(
        ma.fields.Nested(QueueStatsSchema),
        allow_none=True,
        metadata={"description": "Null if the queue server cannot be reached"},
    )


# --- Review items ---------------------------------------------------------------------------------


class ReviewQuerySchema(ma.Schema):
    class Meta:
        unknown = ma.EXCLUDE

    status = ma.fields.Enum(ReviewStatus, by_value=True, load_default=None)


class ReviewItemSchema(ma.Schema):
    id = ma.fields.UUID()
    case_id = ma.fields.UUID()
    case_alias = ma.fields.Function(lambda item: item.case.alias)
    case_status = ma.fields.Function(lambda item: item.case.status.value)
    source = ma.fields.Function(lambda item: item.source.value)
    status = ma.fields.Function(lambda item: item.status.value)
    reason = ma.fields.String()
    payload = ma.fields.Dict()
    resolution = ma.fields.Function(lambda item: item.resolution.value if item.resolution else None)
    resolution_note = ma.fields.String(allow_none=True)
    resolved_at = ma.fields.DateTime(allow_none=True)
    created_at = ma.fields.DateTime()


class ResolveReviewSchema(ma.Schema):
    resolution = ma.fields.Enum(ReviewResolution, by_value=True, required=True)
    note = ma.fields.String(
        required=True, metadata={"description": "Why, in 10 to 500 characters. Required."}
    )


class ReviewRequestSchema(ma.Schema):
    """A family asking for a person to look at their case."""

    message = ma.fields.String(required=True, validate=ma.validate.Length(max=2000))


class ReviewRequestedSchema(ma.Schema):
    """What the family gets back: the request's ID and state, none of the admin's fields."""

    id = ma.fields.UUID()
    status = ma.fields.Function(lambda item: item.status.value)
    created_at = ma.fields.DateTime()


# --- Cases ---------------------------------------------------------------------------------------


class AdminCasesQuerySchema(ma.Schema):
    class Meta:
        unknown = ma.EXCLUDE

    status = ma.fields.Enum(CaseStatus, by_value=True, load_default=None)
    q = ma.fields.String(
        load_default=None,
        validate=ma.validate.Length(max=80),
        metadata={"description": "Text in the alias, slug, hospital name or the owner's email"},
    )


class AdminCaseListItemSchema(ma.Schema):
    id = ma.fields.UUID()
    public_slug = ma.fields.String()
    alias = ma.fields.String()
    status = ma.fields.Function(lambda case: case.status.value)
    trust_level = ma.fields.Function(lambda case: case.trust_level.value)
    hospital_name = ma.fields.Function(lambda case: case.hospital.display_name)
    owner_email = ma.fields.Function(lambda case: case.created_by.email)
    target_kes_minor = ma.fields.Integer(allow_none=True)
    pledged_kes_minor = ma.fields.Integer()
    settled_kes_minor = ma.fields.Integer()
    created_at = ma.fields.DateTime()
    updated_at = ma.fields.DateTime()


class FreezeSchema(ma.Schema):
    reason = ma.fields.String(
        required=True,
        validate=ma.validate.Length(min=10, max=300),
        metadata={"description": "Why the case is being paused (10 to 300 characters)"},
    )


class AdminDonationSchema(ma.Schema):
    id = ma.fields.UUID()
    status = ma.fields.Function(lambda d: d.status.value)
    amount_usd_minor = ma.fields.Integer()
    paypal_fee_usd_minor = ma.fields.Integer(allow_none=True)
    net_usd_minor = ma.fields.Integer()
    allocated_usd_minor = ma.fields.Integer()
    refunded_usd_minor = ma.fields.Integer()
    excess_choice = ma.fields.Function(lambda d: d.excess_choice.value if d.excess_choice else None)
    donor_display_name = ma.fields.Function(
        lambda d: None if d.is_anonymous else d.donor_display_name
    )
    is_anonymous = ma.fields.Boolean()
    captured_at = ma.fields.DateTime(allow_none=True)


class AdminSettlementSchema(ma.Schema):
    id = ma.fields.UUID()
    tranche_no = ma.fields.Integer()
    status = ma.fields.Function(lambda s: s.status.value)
    adapter = ma.fields.Function(lambda s: s.adapter.value)
    amount_usd_minor = ma.fields.Integer()
    amount_kes_minor = ma.fields.Integer()
    external_batch_id = ma.fields.String(allow_none=True)
    failure_reason = ma.fields.String(allow_none=True)
    requested_at = ma.fields.DateTime()
    completed_at = ma.fields.DateTime(allow_none=True)


class AdminRefundSchema(ma.Schema):
    id = ma.fields.UUID()
    donation_id = ma.fields.UUID()
    amount_usd_minor = ma.fields.Integer()
    reason = ma.fields.Function(lambda r: r.reason.value)
    status = ma.fields.Function(lambda r: r.status.value)
    requested_by = ma.fields.Function(lambda r: r.requested_by.value)
    completed_at = ma.fields.DateTime(allow_none=True)


class AuditEntrySchema(ma.Schema):
    """One audit line. The caller's address (even hashed) is not shown."""

    id = ma.fields.UUID()
    created_at = ma.fields.DateTime()
    actor_type = ma.fields.Function(lambda entry: entry.actor_type.value)
    actor_user_id = ma.fields.UUID(allow_none=True)
    action = ma.fields.String()
    target_type = ma.fields.String()
    target_id = ma.fields.UUID(allow_none=True)
    details = ma.fields.Dict()
    request_id = ma.fields.String(allow_none=True)


class AuditQuerySchema(ma.Schema):
    class Meta:
        unknown = ma.EXCLUDE

    target_type = ma.fields.String(load_default=None, validate=ma.validate.Length(max=40))
    target_id = ma.fields.UUID(load_default=None)


class AdminTriageSchema(ma.Schema):
    document_id = ma.fields.UUID()
    risk_score = ma.fields.Integer()
    risk_band = ma.fields.Function(lambda t: t.risk_band.value)
    route = ma.fields.Function(lambda t: t.route.value)
    extracted = ma.fields.Dict()
    signals = ma.fields.List(ma.fields.Raw())
    reasons_en = ma.fields.List(ma.fields.String())
    used_llm = ma.fields.Boolean()
    model_versions = ma.fields.Dict()
    created_at = ma.fields.DateTime()


class AdminConsentSchema(ma.Schema):
    scope = ma.fields.Function(lambda c: c.scope.value)
    actor = ma.fields.Function(lambda c: c.actor.value)
    wording_version = ma.fields.String()
    language = ma.fields.String()
    granted_at = ma.fields.DateTime()
    withdrawn_at = ma.fields.DateTime(allow_none=True)


class AdminOwnerSchema(ma.Schema):
    id = ma.fields.UUID()
    email = ma.fields.String()
    display_name = ma.fields.String()


class AdminCaseSchema(ma.Schema):
    """Everything about one case, for the admin. Written once per read to the audit log."""

    id = ma.fields.UUID()
    public_slug = ma.fields.String()
    alias = ma.fields.String()
    region = ma.fields.String()
    story = ma.fields.String(allow_none=True)
    patient_reference = ma.fields.String()
    is_minor = ma.fields.Boolean()
    status = ma.fields.Function(lambda case: case.status.value)
    trust_level = ma.fields.Function(lambda case: case.trust_level.value)
    close_reason = ma.fields.Function(
        lambda case: case.close_reason.value if case.close_reason else None
    )
    freeze_reason = ma.fields.String(allow_none=True)
    owner = ma.fields.Nested(AdminOwnerSchema, attribute="created_by")
    hospital = ma.fields.Function(
        lambda case: {"id": str(case.hospital.id), "display_name": case.hospital.display_name}
    )
    money = ma.fields.Function(lambda case: MoneySummarySchema().dump(case))
    documents = ma.fields.Function(
        lambda case: DocumentSchema(many=True).dump(document_service.list_documents(case))
    )
    triage = ma.fields.Function(
        lambda case: AdminTriageSchema(many=True).dump(
            sorted(case.triage_results, key=lambda t: t.created_at, reverse=True)
        )
    )
    consents = ma.fields.Function(
        lambda case: AdminConsentSchema(many=True).dump(consent_service.list_for_case(case))
    )
    confirmations = ma.fields.Function(
        lambda case: ConfirmationSchema(many=True).dump(
            case_service.confirmations_newest_first(case)
        )
    )
    donations = ma.fields.Function(
        lambda case: AdminDonationSchema(many=True).dump(
            sorted(case.donations, key=lambda d: d.created_at, reverse=True)
        )
    )
    settlements = ma.fields.Function(
        lambda case: AdminSettlementSchema(many=True).dump(case.settlements)
    )
    refunds = ma.fields.Function(
        lambda case: AdminRefundSchema(many=True).dump(
            [refund for donation in case.donations for refund in donation.refunds]
        )
    )
    review_items = ma.fields.Function(
        lambda case: ReviewItemSchema(many=True).dump(
            sorted(case.review_items, key=lambda i: i.created_at, reverse=True)
        )
    )
    audit_trail = ma.fields.Function(
        lambda case: AuditEntrySchema(many=True).dump(
            admin_service.audit_trail(object_session(case), case)
        )
    )
    created_at = ma.fields.DateTime()
    submitted_at = ma.fields.DateTime(allow_none=True)
    live_at = ma.fields.DateTime(allow_none=True)
    closed_at = ma.fields.DateTime(allow_none=True)
    frozen_at = ma.fields.DateTime(allow_none=True)
