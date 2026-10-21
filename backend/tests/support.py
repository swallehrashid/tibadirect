"""Routes that exist only in tests, to exercise error handling and validation."""

import marshmallow as ma
from flask import Blueprint as FlaskBlueprint
from flask import Flask
from flask.views import MethodView
from flask_smorest import Blueprint
from sqlalchemy import select

from app import extensions
from app.errors import Conflict, NotFound, PermissionDenied
from app.extensions import db
from app.models.hospital import FacilityRegisterEntry
from app.pagination import PaginationArgsSchema, page_of, paginate


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


def register(app: Flask) -> None:
    extensions.api.register_blueprint(smorest_bp)
    app.register_blueprint(plain_bp)
