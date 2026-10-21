"""Job functions. They run inside a worker with an application context.

Each later feature adds its own jobs here and to the schedule in ``scheduler.py`` if periodic.
"""

import logging
from datetime import UTC, datetime

from app.extensions import get_redis

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "tibadirect:scheduler:heartbeat"
HEARTBEAT_TTL_SECONDS = 600


def heartbeat() -> str:
    """Prove the whole chain works: scheduler, queue and worker. The admin console reads this."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    get_redis().set(HEARTBEAT_KEY, stamp, ex=HEARTBEAT_TTL_SECONDS)
    logger.info("heartbeat job ran")
    return stamp
