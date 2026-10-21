"""Public routes: no sign-in needed."""

from datetime import UTC, datetime

from flask import current_app
from flask.views import MethodView

from app.api.base import TibaBlueprint
from app.extensions import db, limiter
from app.models.case import Case
from app.models.ledger import GENESIS_HASH
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.schemas.cases import PublicCaseSchema, PublicCasesQuerySchema
from app.schemas.ledger import CaseVerificationSchema, PublicLedgerRowSchema
from app.schemas.public import PublicConfigSchema
from app.services import case_service, ledger_service

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


# Verifying walks the whole chain, so it is limited more tightly than reading a page.
VERIFY_LIMIT = "20 per minute"


@blp.route("/cases/<string:slug>/ledger")
class PublicLedger(MethodView):
    decorators = [limiter.limit(PUBLIC_READ_LIMIT, scope="public_ledger")]

    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(PublicLedgerRowSchema))
    @blp.alt_response(404, description="No such public case")
    def get(self, paging: dict, slug: str) -> dict:
        """Every money movement on a public case, oldest first, with shortened hashes and
        references. A donor's name shows only if they gave one and did not ask to be anonymous."""
        case = case_service.get_public_case_or_404(db.session, slug)
        page = paginate(db.session, ledger_service.case_entries(case.id), paging)
        page["items"] = ledger_service.public_rows(db.session, page["items"])
        return page


@blp.route("/cases/<string:slug>/ledger/verify")
class PublicLedgerVerify(MethodView):
    decorators = [limiter.limit(VERIFY_LIMIT, scope="public_ledger_verify")]

    @blp.response(200, CaseVerificationSchema)
    @blp.alt_response(404, description="No such public case")
    def get(self, slug: str) -> dict:
        """Recompute the hash chain and report whether this case's ledger is intact."""
        case = case_service.get_public_case_or_404(db.session, slug)
        status = ledger_service.verify_chain(db.session, case.id)
        return {
            "valid": status.valid,
            "entries_checked": status.entries_checked,
            "last_hash": status.case_last_hash or GENESIS_HASH,
            "checked_at": datetime.now(UTC),
        }
