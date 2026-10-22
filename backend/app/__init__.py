"""TibaDirect Flask application factory."""

import os

from dotenv import find_dotenv, load_dotenv
from flask import Flask

from app import extensions
from app.cli import register_cli
from app.config import get_config
from app.errors import register_error_handlers
from app.logging import configure_logging
from app.request_id import init_request_id
from app.security import web
from app.spa import init_spa

__version__ = "0.1.0"


def create_app(config_name: str | None = None) -> Flask:
    """Create and configure the Flask application.

    ``config_name`` is ``development``, ``test`` or ``production``; it defaults to ``APP_ENV``.
    """
    if os.environ.get("APP_ENV", "").lower() != "production":
        load_dotenv(find_dotenv(usecwd=True), override=False)

    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    configure_logging(app)
    extensions.init_extensions(app)
    from app import models  # noqa: F401  (registers every model with the metadata)

    init_request_id(app)
    register_error_handlers(app)
    register_cli(app)
    _register_blueprints()
    web.exempt_health_from_https_redirect(app)
    init_spa(app)
    return app


def _register_blueprints() -> None:
    from app.api.admin import blp as admin_blp
    from app.api.agent import blp as agent_blp
    from app.api.auth import blp as auth_blp
    from app.api.cases import blp as cases_blp
    from app.api.checker import blp as checker_blp
    from app.api.consents import blp as consents_blp
    from app.api.documents import blp as documents_blp
    from app.api.donations import blp as donations_blp
    from app.api.health import blp as health_blp
    from app.api.hospital_portal import blp as hospital_portal_blp
    from app.api.hospitals import blp as hospitals_blp
    from app.api.public import blp as public_blp
    from app.api.webhooks import blp as webhooks_blp

    for blueprint in (
        health_blp,
        auth_blp,
        public_blp,
        checker_blp,
        admin_blp,
        agent_blp,
        hospitals_blp,
        consents_blp,
        cases_blp,
        documents_blp,
        donations_blp,
        hospital_portal_blp,
        webhooks_blp,
    ):
        extensions.api.register_blueprint(blueprint)
