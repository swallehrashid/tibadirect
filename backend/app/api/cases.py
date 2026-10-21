"""A family's own cases."""

import uuid

from flask.views import MethodView
from flask_login import current_user
from sqlalchemy import select

from app.api.base import TibaBlueprint
from app.extensions import db, limiter
from app.models.case import Case
from app.models.enums import UserRole
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.schemas.admin import ReviewRequestedSchema, ReviewRequestSchema
from app.schemas.cases import (
    CreateCaseSchema,
    FamilyCaseListItemSchema,
    FamilyCaseSchema,
    UpdateCaseSchema,
)
from app.security.permissions import current_account, get_owned_case_or_404
from app.services import case_service, review_service

blp = TibaBlueprint("cases", __name__, url_prefix="/api/cases", description="My cases")


def _per_user() -> str:
    return f"user:{current_user.get_id()}"


@blp.route("")
class Cases(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.arguments(PaginationArgsSchema, location="query")
    @blp.response(200, page_of(FamilyCaseListItemSchema))
    def get(self, paging: dict) -> dict:
        """My cases, newest first."""
        statement = (
            select(Case)
            .where(Case.created_by_id == current_account().id)
            .order_by(Case.created_at.desc(), Case.id)
        )
        return paginate(db.session, statement, paging)

    @blp.secured(UserRole.FAMILY)
    @limiter.limit("10 per hour", key_func=_per_user, scope="case_create")
    @blp.arguments(CreateCaseSchema)
    @blp.response(201, FamilyCaseSchema)
    def post(self, payload: dict) -> Case:
        """Start a draft case for a hospital bill."""
        case = case_service.create_draft(db.session, current_account(), **payload)
        db.session.commit()
        return case


@blp.route("/<uuid:case_id>")
class CaseDetail(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.response(200, FamilyCaseSchema)
    def get(self, case_id: uuid.UUID) -> Case:
        """One of my cases, with its timeline, latest checks, confirmations and money."""
        return get_owned_case_or_404(case_id, current_account())

    @blp.secured(UserRole.FAMILY)
    @blp.arguments(UpdateCaseSchema)
    @blp.response(200, FamilyCaseSchema)
    @blp.alt_response(409, description="Only a draft can be edited")
    def patch(self, payload: dict, case_id: uuid.UUID) -> Case:
        """Edit a draft: alias, region, story, patient reference, minor flag, photo choice."""
        user = current_account()
        case = get_owned_case_or_404(case_id, user)
        case_service.update_draft(db.session, case, user, payload)
        db.session.commit()
        return case

    @blp.secured(UserRole.FAMILY)
    @blp.response(204)
    @blp.alt_response(409, description="Only a draft can be deleted")
    def delete(self, case_id: uuid.UUID) -> str:
        """Delete a draft (it becomes cancelled)."""
        user = current_account()
        case_service.delete_draft(get_owned_case_or_404(case_id, user), user)
        db.session.commit()
        return ""


@blp.route("/<uuid:case_id>/review-request")
class ReviewRequest(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.arguments(ReviewRequestSchema)
    @blp.response(201, ReviewRequestedSchema)
    @blp.alt_response(409, description="Already asked, or the case is not in play")
    def post(self, payload: dict, case_id: uuid.UUID) -> object:
        """Ask for a person to look at your case. One open request at a time."""
        user = current_account()
        case = get_owned_case_or_404(case_id, user)
        item = review_service.request_review(db.session, case, user, payload["message"])
        db.session.commit()
        return item
