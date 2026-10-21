"""Authentication routes: email-code sign-in, session and profile."""

from flask import Response, make_response, request, session
from flask.views import MethodView
from flask_limiter.util import get_remote_address
from flask_login import login_user, logout_user
from flask_wtf.csrf import generate_csrf

from app.api.base import TibaBlueprint
from app.extensions import db, limiter
from app.models.enums import UserRole
from app.models.identity import User
from app.schemas.auth import (
    AcceptedSchema,
    CsrfTokenSchema,
    RequestCodeSchema,
    SessionSchema,
    UpdateMeSchema,
    UserSchema,
    VerifyCodeSchema,
)
from app.security import crypto
from app.security.permissions import active_staff_profile, current_account
from app.services import auth_service

blp = TibaBlueprint("auth", __name__, url_prefix="/api/auth", description="Sign-in and sessions")

_WINDOW = "15 minutes"


def _email_key() -> str:
    """Rate-limit bucket per email address (hashed, so no address reaches Redis).

    Requests without a usable address share the caller's IP bucket.
    """
    body = request.get_json(silent=True)
    email = body.get("email") if isinstance(body, dict) else None
    if isinstance(email, str) and email.strip():
        return "email:" + crypto.lookup_hash(auth_service.normalize_email(email), context="rl")
    return "ip:" + get_remote_address()


def serialize_user(user: User) -> dict:
    """The signed-in user's own view of their account."""
    hospital = None
    if user.role == UserRole.HOSPITAL_STAFF and (profile := active_staff_profile(user)):
        hospital = {
            "id": profile.hospital.id,
            "display_name": profile.hospital.display_name,
            "county": profile.hospital.county,
            "staff_role": profile.staff_role.value,
        }
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "preferred_language": user.preferred_language,
        "hospital": hospital,
    }


@blp.route("/csrf")
class Csrf(MethodView):
    @blp.response(200, CsrfTokenSchema)
    def get(self) -> Response:
        """Issue a CSRF token for the current session. Fetch a new one after signing in or out."""
        response = make_response({"csrf_token": generate_csrf()})
        response.headers["Cache-Control"] = "no-store"
        return response


@blp.route("/request-code")
class RequestCode(MethodView):
    @limiter.limit(f"5 per {_WINDOW}", scope="request_code_ip")
    @limiter.limit(f"5 per {_WINDOW}", key_func=_email_key, scope="request_code_email")
    @blp.arguments(RequestCodeSchema)
    @blp.response(202, AcceptedSchema)
    def post(self, payload: dict) -> dict:
        """Email a six-digit sign-in code. The answer never reveals whether an account exists."""
        auth_service.request_code(db.session, payload["email"], ip=get_remote_address())
        return {"message": "If that address can receive email, a sign-in code is on its way."}


@blp.route("/verify")
class Verify(MethodView):
    @limiter.limit("10 per 15 minutes", scope="verify_ip")
    @limiter.limit("10 per 15 minutes", key_func=_email_key, scope="verify_email")
    @blp.arguments(VerifyCodeSchema)
    @blp.response(200, SessionSchema)
    @blp.alt_response(401, description="The code is wrong, expired, used or locked")
    def post(self, payload: dict) -> dict:
        """Check a code and sign in. The first successful sign-in creates a family account."""
        user, _created = auth_service.verify_code(db.session, payload["email"], payload["code"])
        session.clear()  # a new session on sign-in, so a planted cookie cannot be reused
        login_user(user)
        session.permanent = True
        return {"user": serialize_user(user)}


@blp.route("/logout")
class Logout(MethodView):
    @blp.response(204)
    def post(self) -> str:
        """End the session. Safe to call when already signed out."""
        logout_user()
        session.clear()
        return ""


@blp.route("/me")
class Me(MethodView):
    @blp.secured()
    @blp.response(200, UserSchema)
    def get(self) -> dict:
        """The signed-in user, with their role and hospital if they are staff."""
        return serialize_user(current_account())

    @blp.secured()
    @blp.arguments(UpdateMeSchema)
    @blp.response(200, UserSchema)
    def patch(self, payload: dict) -> dict:
        """Change the display name or the language used in emails and the interface."""
        user = current_account()
        for field, value in payload.items():
            setattr(user, field, value)
        db.session.commit()
        return serialize_user(user)
