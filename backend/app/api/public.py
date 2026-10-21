"""Public routes: no sign-in needed."""

from flask import current_app
from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.extensions import db, limiter
from app.models.case import Case
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.schemas.cases import PublicCaseSchema, PublicCasesQuerySchema
from app.schemas.public import PublicConfigSchema
from app.services import case_service

blp = TibaBlueprint(
    "public", __name__, url_prefix="/api/public", description="Public pages and data"
)

# Public pages can be scraped, so each caller address gets a generous but finite allowance.
PUBLIC_READ_LIMIT = "120 per minute"


@blp.route("/config")
class PublicConfig(MethodView):
    @blp.response(200, PublicConfigSchema)
    def get(self) -> dict:
        """Settings the browser needs before anything else, with no secrets in them."""
        config = current_app.config
        return {
            "paypal_client_id": config["PAYPAL_CLIENT_ID"],
            "paypal_mode": config["PAYPAL_MODE"],
            "currency": "USD",
            "demo_banner": config["DEMO_BANNER"],
            "features": {
                "agent": config["AGENT_ENABLED"],
                "appeal_checker": config["APPEAL_CHECKER_ENABLED"],
            },
        }


@blp.route("/cases")
class PublicCases(MethodView):
    decorators = [limiter.limit(PUBLIC_READ_LIMIT, scope="public_cases")]

    @blp.arguments(PublicCasesQuerySchema, location="query")
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(PublicCaseSchema))
    def get(self, filters: dict, paging: dict) -> dict:
        """Live and funded cases whose families agreed to a public page. ``?county=`` narrows
        by the hospital's county."""
        return paginate(db.session, case_service.public_cases(filters["county"]), paging)


@blp.route("/cases/<string:slug>")
class PublicCaseDetail(MethodView):
    decorators = [limiter.limit(PUBLIC_READ_LIMIT, scope="public_case")]

    @blp.response(200, PublicCaseSchema)
    @blp.alt_response(404, description="No such public case")
    def get(self, slug: str) -> Case:
        """One public case. A case that is not public is reported as not found."""
        return case_service.get_public_case_or_404(db.session, slug)
