"""TibaDirect Flask application factory."""

import os

from dotenv import find_dotenv, load_dotenv
from flask import Flask

from app import extensions
from app.config import get_config
from app.errors import register_error_handlers
from app.logging import configure_logging
from app.request_id import init_request_id

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
    init_request_id(app)
    register_error_handlers(app)
    _register_blueprints()
    return app


def _register_blueprints() -> None:
    from app.api.health import blp as health_blp

    extensions.api.register_blueprint(health_blp)
