"""Bills, submission and the result of the automatic checks."""

import uuid

from flask.views import MethodView
from flask_login import current_user

from app.api.base import TibaBlueprint
from app.extensions import db, limiter
from app.models.case import Case
from app.models.enums import UserRole
from app.models.identity import User
from app.schemas.cases import FamilyCaseSchema
from app.schemas.documents import (
    DocumentListSchema,
    DocumentSchema,
    TriageStatusSchema,
    UploadFileSchema,
    UploadFormSchema,
)
from app.security.permissions import current_account, get_owned_case_or_404
from app.services import case_service, document_service

blp = TibaBlueprint("documents", __name__, url_prefix="/api/cases", description="Bills and checks")


def _per_user() -> str:
    return f"user:{current_user.get_id()}"


def _owned(case_id: uuid.UUID) -> tuple[User, Case]:
    user = current_account()
    return user, get_owned_case_or_404(case_id, user)


@blp.route("/<uuid:case_id>/documents")
class Documents(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.response(200, DocumentListSchema)
    def get(self, case_id: uuid.UUID) -> dict:
        """The case's bills: type, size, status and a short-lived link to your own upload."""
        _, case = _owned(case_id)
        return {"items": document_service.list_documents(case)}

    @blp.secured(UserRole.FAMILY)
    @limiter.limit("20 per hour", key_func=_per_user, scope="upload")
    @blp.arguments(UploadFormSchema, location="form")
    @blp.arguments(UploadFileSchema, location="files")
    @blp.response(201, DocumentSchema)
    @blp.alt_response(409, description="Case is not a draft, too many bills or a duplicate file")
    @blp.alt_response(413, description="The file is larger than 10 MB")
    @blp.alt_response(415, description="Not a PDF, PNG or JPEG")
    def post(self, form: dict, files: dict, case_id: uuid.UUID) -> object:
        """Upload a bill (PDF, PNG or JPEG, up to 10 MB). The file name is never kept."""
        user, case = _owned(case_id)
        upload = files["file"]
        document = document_service.add_document(
            db.session,
            case,
            user,
            data=upload.stream.read(),
            filename=upload.filename,
            declared_type=upload.mimetype,
            kind=form["kind"],
        )
        db.session.commit()
        return document


@blp.route("/<uuid:case_id>/documents/<uuid:document_id>")
class DocumentDetail(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.response(204)
    @blp.alt_response(404, description="No such document on this case")
    def delete(self, case_id: uuid.UUID, document_id: uuid.UUID) -> str:
        """Remove a bill from a draft case."""
        user, case = _owned(case_id)
        document_service.remove_document(db.session, case, user, document_id)
        db.session.commit()
        return ""


@blp.route("/<uuid:case_id>/submit")
class Submit(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.response(202, FamilyCaseSchema)
    @blp.alt_response(
        409, description="A consent or the bill is missing, or the case is not a draft"
    )
    def post(self, case_id: uuid.UUID) -> Case:
        """Send the case for checking. Needs both required consents and at least one bill."""
        user, case = _owned(case_id)
        bills = document_service.submit_case(db.session, case, user)
        db.session.commit()
        document_service.enqueue_triage(bills)
        return case


@blp.route("/<uuid:case_id>/triage")
class Triage(MethodView):
    @blp.secured(UserRole.FAMILY)
    @blp.response(200, TriageStatusSchema)
    def get(self, case_id: uuid.UUID) -> dict:
        """The latest result of the automatic checks, in plain language. Never a raw score."""
        _, case = _owned(case_id)
        result = case_service.latest_triage(case)
        if result is None:
            return {
                "ready": False,
                "risk_band": None,
                "route": None,
                "reasons_en": [],
                "reasons_sw": [],
                "checked_at": None,
            }
        return {
            "ready": True,
            "risk_band": result.risk_band.value,
            "route": result.route.value,
            "reasons_en": result.reasons_en,
            "reasons_sw": result.reasons_sw,
            "checked_at": result.created_at,
        }
