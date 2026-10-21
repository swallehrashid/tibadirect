"""Admin console routes."""

import uuid
from datetime import UTC, datetime

from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.extensions import db
from app.models.enums import UserRole
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.schemas.admin import (
    AdminCaseListItemSchema,
    AdminCaseSchema,
    AdminCasesQuerySchema,
    AdminStatsSchema,
    AuditEntrySchema,
    AuditQuerySchema,
    FreezeSchema,
    JobsOverviewSchema,
    ResolveReviewSchema,
    RetriedJobSchema,
    ReviewItemSchema,
    ReviewQuerySchema,
)
from app.schemas.ledger import ChainVerificationSchema
from app.security.permissions import current_account, get_case_for_admin_or_404
from app.services import (
    admin_service,
    audit_service,
    job_service,
    ledger_service,
    review_service,
)
from app.services.audit_service import Target

blp = TibaBlueprint("admin", __name__, url_prefix="/api/admin", description="Admin console")


@blp.route("/jobs")
class Jobs(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.response(200, JobsOverviewSchema)
    def get(self) -> dict:
        """Queue lengths, failed jobs and whether the scheduler is alive."""
        return job_service.overview()


@blp.route("/jobs/<string:job_id>/retry")
class RetryJob(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.response(200, RetriedJobSchema)
    @blp.alt_response(404, description="No such job")
    @blp.alt_response(409, description="The job has not failed")
    def post(self, job_id: str) -> dict:
        """Put a failed job back on its queue."""
        job = job_service.retry_failed(job_id)
        audit_service.record(
            "job.retried",
            Target("job", None),
            actor=current_account(),
            details={"job_id": job.id, "queue": job.origin, "function": job.func_name},
        )
        db.session.commit()
        return {"id": job.id, "queue": job.origin, "status": job.get_status().value}


@blp.route("/ledger/verify")
class VerifyLedger(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.response(200, ChainVerificationSchema)
    def get(self) -> dict:
        """Recompute the whole hash chain. If it is broken, names the first entry that fails."""
        status = ledger_service.verify_chain(db.session)
        audit_service.record(
            "ledger.verified",
            Target("ledger", None),
            actor=current_account(),
            details={"valid": status.valid, "entries_checked": status.entries_checked},
        )
        db.session.commit()
        return {
            "valid": status.valid,
            "entries_checked": status.entries_checked,
            "last_hash": status.last_hash,
            "first_bad_seq": status.first_bad_seq,
            "checked_at": datetime.now(UTC),
        }


@blp.route("/stats")
class Stats(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.response(200, AdminStatsSchema)
    def get(self) -> dict:
        """Cases by status, money totals, what is waiting for a person and the queue sizes."""
        result = admin_service.stats(db.session)
        try:
            result["queues"] = job_service.overview()["queues"]
        except Exception:  # the queue server being down must not hide the rest
            result["queues"] = None
        return result


@blp.route("/review-items")
class ReviewItems(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.arguments(ReviewQuerySchema, location="query")
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(ReviewItemSchema))
    def get(self, filters: dict, paging: dict) -> dict:
        """The review queue. Open items come first, the longest waiting on top."""
        return paginate(db.session, review_service.queue(filters["status"]), paging)


@blp.route("/review-items/<uuid:item_id>/resolve")
class ResolveReviewItem(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.arguments(ResolveReviewSchema)
    @blp.response(200, ReviewItemSchema)
    @blp.alt_response(409, description="Already resolved, or the case cannot take that decision")
    def post(self, payload: dict, item_id: uuid.UUID) -> object:
        """Resolve an item with a decision and a written reason (the reason is required)."""
        item = review_service.get_or_404(db.session, item_id)
        review_service.resolve(
            db.session, item, current_account(), payload["resolution"], payload["note"]
        )
        db.session.commit()
        return item


@blp.route("/cases")
class AdminCases(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.arguments(AdminCasesQuerySchema, location="query")
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(AdminCaseListItemSchema))
    def get(self, filters: dict, paging: dict) -> dict:
        """All cases, newest first. Filter by ``status`` and ``q`` (alias, slug, hospital)."""
        return paginate(db.session, admin_service.cases(filters["status"], filters["q"]), paging)


@blp.route("/cases/<uuid:case_id>")
class AdminCaseDetail(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.response(200, AdminCaseSchema)
    def get(self, case_id: uuid.UUID) -> object:
        """Everything about one case. Opening it is written to the audit log."""
        case = get_case_for_admin_or_404(case_id)
        audit_service.record("admin.case_viewed", case, actor=current_account())
        db.session.commit()
        return case


@blp.route("/cases/<uuid:case_id>/freeze")
class FreezeCase(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.arguments(FreezeSchema)
    @blp.response(200, AdminCaseSchema)
    @blp.alt_response(409, description="The case cannot be frozen from its current status")
    def post(self, payload: dict, case_id: uuid.UUID) -> object:
        """Pause a case: it stops being public and stops taking money at once."""
        case = get_case_for_admin_or_404(case_id)
        admin_service.freeze(case, current_account(), payload["reason"])
        db.session.commit()
        return case


@blp.route("/audit-log")
class AuditLogList(MethodView):
    @blp.secured(UserRole.ADMIN)
    @blp.arguments(AuditQuerySchema, location="query")
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(AuditEntrySchema))
    def get(self, filters: dict, paging: dict) -> dict:
        """The audit log, newest first. Filter with ``target_type`` and ``target_id``."""
        statement = admin_service.audit_entries(filters["target_type"], filters["target_id"])
        return paginate(db.session, statement, paging)
