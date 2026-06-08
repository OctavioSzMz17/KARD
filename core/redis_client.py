"""
Lazy, fault-tolerant Redis client.

Used for ephemeral auth state: failed-login counters / account
lockouts (Rules.md section 1.8) and, later, the JWT logout
blacklist (Rules.md section 1.7).

Connecting is best-effort: if Redis is unreachable, the affected
features degrade gracefully (no lockout / no blacklist) and a
warning is logged, rather than the whole API going down because a
side-feature's cache is offline.
"""

import logging
from typing import Optional

import redis

from core.config import settings

logger = logging.getLogger("kard.redis")

_client: Optional[redis.Redis] = None
_unavailable = False


def get_redis() -> Optional[redis.Redis]:
    """Return a shared Redis client, or None if Redis cannot be reached."""
    global _client, _unavailable

    if _unavailable:
        return None

    if _client is None:
        try:
            client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            client.ping()
            _client = client
        except Exception as exc:  # pragma: no cover - depends on infra availability
            logger.warning("Redis unavailable at %s (%s); continuing without it", settings.REDIS_URL, exc)
            _unavailable = True
            return None

    return _client
