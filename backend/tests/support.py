"""Routes that exist only in tests, to exercise error handling and validation."""

import uuid

import marshmallow as ma
from flask import Blueprint as FlaskBlueprint
from flask import Flask, g
from flask.views import MethodView
from flask_smorest import Blueprint
from sqlalchemy import select

from app import extensions
from app.api.base import TibaBlueprint
from app.errors import Conflict, NotFound, PermissionDenied
from app.extensions import db
from app.models.enums import UserRole
from app.models.hospital import FacilityRegisterEntry
from app.pagination import PaginationArgsSchema, page_of, paginate
from app.security.permissions import current_account, current_staff, get_case_for_user_or_404


class EchoSchema(ma.Schema):
    name = ma.fields.String(required=True, validate=ma.validate.Length(min=2))
    amount = ma.fields.Integer(required=True)


smorest_bp = Blueprint("test_support", __name__, url_prefix="/api/_test", description="test only")


@smorest_bp.route("/echo")
class Echo(MethodView):
    @smorest_bp.arguments(EchoSchema)
    @smorest_bp.response(200, EchoSchema)
    def post(self, payload: dict) -> dict:
        return payload


class RegisterEntrySchema(ma.Schema):
    register_code = ma.fields.String()
    name = ma.fields.String()


@smorest_bp.route("/pages")
class Pages(MethodView):
    @smorest_bp.arguments(PaginationArgsSchema, location="query")
    @smorest_bp.response(200, page_of(RegisterEntrySchema))
    def get(self, args: dict) -> dict:
        statement = select(FacilityRegisterEntry).order_by(FacilityRegisterEntry.register_code)
        return paginate(db.session, statement, args)


@smorest_bp.route("/limited")
class Limited(MethodView):
    decorators = [extensions.limiter.limit("2 per minute")]

    @smorest_bp.response(200)
    def get(self) -> dict:
        return {"ok": True}

    @smorest_bp.response(200)
    def post(self) -> dict:
        return {"ok": True}


secured_bp = TibaBlueprint("test_secured", __name__, url_prefix="/api/_test/secured")


@secured_bp.route("/anyone")
class Anyone(MethodView):
    @secured_bp.secured()
    @secured_bp.response(200)
    def get(self) -> dict:
        return {"user": current_account().email}


@secured_bp.route("/family")
class FamilyOnly(MethodView):
    @secured_bp.secured(UserRole.FAMILY)
    @secured_bp.response(200)
    def get(self) -> dict:
        return {"ok": True}


@secured_bp.route("/staff")
class StaffOnly(MethodView):
    @secured_bp.secured(UserRole.HOSPITAL_STAFF)
    @secured_bp.response(200)
    def get(self) -> dict:
        return {"hospital_id": str(current_staff().hospital_id)}


@secured_bp.route("/admin")
class AdminOnly(MethodView):
    @secured_bp.secured(UserRole.ADMIN)
    @secured_bp.response(200)
    def post(self) -> dict:
        return {"ok": True}


@secured_bp.route("/family-or-admin")
class FamilyOrAdmin(MethodView):
    @secured_bp.secured(UserRole.FAMILY, UserRole.ADMIN)
    @secured_bp.response(200)
    def get(self) -> dict:
        return {"ok": True}


@secured_bp.route("/cases/<uuid:case_id>")
class CaseByRole(MethodView):
    @secured_bp.secured()
    @secured_bp.response(200)
    def get(self, case_id: uuid.UUID) -> dict:
        return {"id": str(get_case_for_user_or_404(case_id, current_account()).id)}


plain_bp = FlaskBlueprint("test_plain", __name__, url_prefix="/api/_test")


@plain_bp.get("/boom")
def boom() -> str:
    raise RuntimeError("secret detail for jane.doe@example.test 0712345678")


@plain_bp.get("/not-found")
def not_found() -> str:
    raise NotFound("Case not found.", code="case_not_found")


@plain_bp.get("/conflict")
def conflict() -> str:
    raise Conflict("Invalid transition.", code="invalid_transition", details={"from": "draft"})


@plain_bp.get("/forbidden")
def forbidden() -> str:
    raise PermissionDenied()


def _forget_per_request_state() -> None:
    """Give every test request the empty ``g`` a real request starts with.

    The test transaction keeps one app context open, so Flask-Login's cached user and Flask-WTF's
    cached CSRF token would otherwise carry from one client's request to the next.
    """
    g.pop("_login_user", None)
    g.pop("csrf_token", None)


def register(app: Flask) -> None:
    app.before_request(_forget_per_request_state)
    extensions.api.register_blueprint(smorest_bp)
    extensions.api.register_blueprint(secured_bp)
    app.register_blueprint(plain_bp)
