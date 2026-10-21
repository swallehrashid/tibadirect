"""Authentication routes."""

from flask import Response, make_response
from flask.views import MethodView
from flask_smorest import Blueprint
from flask_wtf.csrf import generate_csrf

from app.schemas.auth import CsrfTokenSchema

blp = Blueprint("auth", __name__, url_prefix="/api/auth", description="Sign-in and sessions")


@blp.route("/csrf")
class Csrf(MethodView):
    @blp.response(200, CsrfTokenSchema)
    def get(self) -> Response:
        """Issue a CSRF token for the current session."""
        response = make_response({"csrf_token": generate_csrf()})
        response.headers["Cache-Control"] = "no-store"
        return response
