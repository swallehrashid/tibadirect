"""Dependency checks behind the health endpoint."""

import logging
from dataclasses import dataclass

from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthStatus:
    status: str  # "ok", "degraded" (Redis down) or "error" (database down)
    version: str
    db: str
    redis: str

    @property
    def http_status(self) -> int:
        return 503 if self.status == "error" else 200


def check_database(session: Session) -> bool:
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Health check: database unreachable")
        session.rollback()
        return False
    return True


def check_redis(client: Redis) -> bool:
    try:
        return bool(client.ping())
    except Exception:
        logger.warning("Health check: Redis unreachable")
        return False


def get_health(session: Session, redis_client: Redis, version: str) -> HealthStatus:
    """The database is required (503 without it); Redis down only degrades the service."""
    db_up = check_database(session)
    redis_up = check_redis(redis_client)
    if not db_up:
        status = "error"
    elif not redis_up:
        status = "degraded"
    else:
        status = "ok"
    return HealthStatus(
        status=status,
        version=version,
        db="up" if db_up else "down",
        redis="up" if redis_up else "down",
    )
