"""Flask extension instances. They are created here and bound to the app in ``init_extensions``."""

from flask import Flask, current_app
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFProtect
from redis import Redis

from app.db_base import Base
from app.errors import TibaApi

db = SQLAlchemy(model_class=Base)
migrate = Migrate()
api = TibaApi()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)
talisman = Talisman()
login_manager = LoginManager()


def init_extensions(app: Flask) -> None:
    from app.security import permissions, web

    web.apply_proxy_fix(app)
    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    api.init_app(app)
    web.declare_security_schemes(api)
    login_manager.init_app(app)
    login_manager.session_protection = "basic"
    login_manager.user_loader(permissions.user_loader)
    login_manager.unauthorized_handler(permissions.unauthorized)
    csrf.init_app(app)
    limiter.init_app(app)
    web.init_security_headers(app, talisman)
    web.init_cors(app)


def get_redis() -> Redis:
    """Return the app's Redis client, created on first use with short timeouts."""
    client = current_app.extensions.get("redis")
    if client is None:
        client = Redis.from_url(
            current_app.config["REDIS_URL"],
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        current_app.extensions["redis"] = client
    return client
