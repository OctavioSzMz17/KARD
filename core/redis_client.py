"""
Lazy, fault-tolerant Redis client.

Used for ephemeral auth state — failed-login counters / account lockouts
(Rules.md section 1.8) and the JWT logout blacklist (Rules.md section 1.7) —
for the storefront catalog cache, and for the consumer cart.

Connecting is best-effort: if Redis is unreachable, most callers degrade
gracefully (no lockout / no blacklist / cache miss) and a warning is logged,
rather than the whole API going down because a side-feature's cache is
offline. The cart is the exception: it has no DB fallback and returns 503
(see `cart/service.py`), which makes a failed connection here user-visible.

Because of that, an unreachable Redis is treated as *temporary*: after a
failure we back off for RETRY_COOLDOWN_SECONDS and then try again, instead
of latching off for the lifetime of the process. A one-second network hiccup
must not disable the cart until the next redeploy.
"""

import logging
import time
from typing import Optional

import redis

from core.config import settings

logger = logging.getLogger("kard.redis")

# Generous enough for a managed Redis across a cloud network (the previous
# 1s tripped on ordinary cold-start latency), short enough that a request
# is not left hanging when Redis is genuinely down.
CONNECT_TIMEOUT_SECONDS = 5
SOCKET_TIMEOUT_SECONDS = 5

# How long to keep serving None before retrying a failed connection. Without
# this, every request would pay the full connect timeout while Redis is down.
RETRY_COOLDOWN_SECONDS = 30.0

_client: Optional[redis.Redis] = None
_retry_after: float = 0.0


def get_redis() -> Optional[redis.Redis]:
    """
    Return a shared Redis client, or None if Redis cannot be reached.

    A None result is not permanent: the next call after the cooldown
    window retries the connection.
    """
    global _client, _retry_after

    if _client is not None:
        return _client

    if time.monotonic() < _retry_after:
        return None

    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
            socket_timeout=SOCKET_TIMEOUT_SECONDS,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        client.ping()
    except Exception as exc:  # pragma: no cover - depends on infra availability
        logger.warning(
            "Redis unavailable at %s (%s); retrying in %.0fs",
            settings.REDIS_URL,
            exc,
            RETRY_COOLDOWN_SECONDS,
        )
        _retry_after = time.monotonic() + RETRY_COOLDOWN_SECONDS
        return None

    logger.info("Redis connection established")
    _client = client
    return _client


STOREFRONT_CACHE_TTL = 300  # 5 minutes


# Tres capas por negocio, todas bajo el mismo slug para invalidarlas juntas:
#   tenant  slug -> tenant_id         (evita la consulta que resuelve el negocio)
#   info    respuesta pública completa del negocio
#   catalog páginas del catálogo (una clave por página/tamaño/búsqueda)
def storefront_tenant_key(slug: str) -> str:
    return f"storefront:tenant:{slug}"


def storefront_info_key(slug: str) -> str:
    return f"storefront:info:{slug}"


# Explorar (listado de negocios y categorías) es global, no de un negocio: sus
# claves cuelgan de `explore:` y cualquier cambio visible en un negocio las vacía
# todas (son pocas y se reconstruyen con una consulta).
EXPLORE_CACHE_TTL = 300  # 5 minutes


def explore_categories_key() -> str:
    return "explore:categories"


def explore_list_key(category: Optional[str], page: int, page_size: int) -> str:
    return f"explore:list:{category or '-'}:{page}:{page_size}"


def invalidate_explore_cache() -> None:
    """
    Vacía el listado y las categorías de explorar.

    Se llama cuando un negocio cambia algo que ahí se ve (logo, banner,
    eslogan). Falla en silencio: la caché es una optimización, no un requisito
    de corrección, y de todos modos caduca sola a los 5 minutos.
    """
    redis_client = get_redis()
    if redis_client is None:
        return
    try:
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match="explore:*", count=100)
            if keys:
                redis_client.delete(*keys)
            if cursor == 0:
                break
    except Exception:
        logger.warning("Failed to invalidate explore cache", exc_info=True)


def invalidate_storefront_cache(slug: str) -> None:
    """
    Delete everything cached for a storefront slug: the slug→tenant
    mapping, the public business info and every catalog page.

    Called after any change that a visitor could see (products, images,
    branding, profile, fulfillment) so the next request re-reads from the
    DB. Fails silently — the cache is an optimization, not a correctness
    requirement.
    """
    redis_client = get_redis()
    if redis_client is None:
        return
    try:
        redis_client.delete(storefront_tenant_key(slug), storefront_info_key(slug))
        pattern = f"storefront:catalog:{slug}:*"
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
            if keys:
                redis_client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        if deleted:
            logger.debug("Invalidated %d cache key(s) for storefront slug=%s", deleted, slug)
    except Exception:
        logger.warning("Failed to invalidate storefront cache for slug=%s", slug, exc_info=True)
