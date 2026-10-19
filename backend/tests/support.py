"""Routes that exist only in tests, to exercise error handling and validation."""

import marshmallow as ma
from flask import Blueprint as FlaskBlueprint
from flask import Flask
from flask.views import MethodView
from flask_smorest import Blueprint

from app import extensions
from app.errors import Conflict, NotFound, PermissionDenied


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
