"""Public routes: no sign-in needed."""

from flask import current_app
from flask.views import MethodView
from flask_smorest import Blueprint

from app.schemas.public import PublicConfigSchema

blp = Blueprint("public", __name__, url_prefix="/api/public", description="Public pages and data")


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
