"""Admin console routes."""

from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.extensions import db
from app.models.enums import UserRole
from app.schemas.admin import JobsOverviewSchema, RetriedJobSchema
from app.security.permissions import current_account
from app.services import audit_service, job_service
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
