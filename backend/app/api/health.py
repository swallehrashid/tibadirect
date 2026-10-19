"""Health check route."""

from dataclasses import asdict

from flask import current_app
from flask.views import MethodView
from flask_smorest import Blueprint

from app.extensions import db, get_redis
from app.schemas.health import HealthSchema
from app.services import health_service

blp = Blueprint("health", __name__, url_prefix="/api", description="Service health")


@blp.route("/health")
class Health(MethodView):
    @blp.response(200, HealthSchema)
    @blp.alt_response(503, schema=HealthSchema, description="The database is unreachable")
    def get(self) -> tuple[dict[str, str], int]:
        """Report the service version and whether the database and Redis are reachable."""
        result = health_service.get_health(
            db.session, get_redis(), current_app.config["APP_VERSION"]
        )
        return asdict(result), result.http_status
