"""Flask extension instances. They are created here and bound to the app in ``init_extensions``."""

from flask import Flask, current_app
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from redis import Redis

from app.errors import TibaApi

db = SQLAlchemy()
migrate = Migrate()
api = TibaApi()


def init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    api.init_app(app)


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
