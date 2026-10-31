"""
Business logic for the explore / discovery module.

Explorar es lo primero que ve un cliente y no depende de quién lo pida, así que
el listado y las categorías se guardan en Redis (cache-aside, 5 min) en lugar de
consultar la base de datos —que cuesta unos 60 ms por consulta— en cada visita.
Cambiar el logo, el banner o el eslogan de un negocio vacía esas claves
(settings/service.py); cualquier otro cambio se refleja al caducar el TTL.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from core.redis_client import (
    EXPLORE_CACHE_TTL,
    explore_categories_key,
    explore_list_key,
    get_redis,
)
from core.tenant_config import read_branding, read_profile
from explore.models import CategoriesResponse, CategoryItem, ExploreResponse, ExploreStoreItem
from explore.repository import ExploreRepository

logger = logging.getLogger("kard.explore")


class ExploreService:
    def __init__(self, db: Session):
        self.repo = ExploreRepository(db)

    def get_categories(self) -> CategoriesResponse:
        """Return business types that have at least one active public storefront."""
        key = explore_categories_key()
        cached = self._read(key, CategoriesResponse)
        if cached is not None:
            return cached

        rows = self.repo.get_categories()
        result = CategoriesResponse(
            categories=[
                CategoryItem(business_type=r["business_type"], store_count=r["store_count"])
                for r in rows
            ]
        )
        self._write(key, result)
        return result

    def list_stores(
        self,
        category: Optional[str],
        search: Optional[str],
        page: int,
        page_size: int,
    ) -> ExploreResponse:
        """
        Paginated public store listing with optional category and search filters.

        Las búsquedas NO se guardan: son texto libre, casi nunca se repiten y
        cada término distinto sería una clave nueva en Redis (un endpoint público
        podría llenarlo). Lo que se repite —el listado por categoría y página— sí.
        """
        key = None if search else explore_list_key(category, page, page_size)
        if key is not None:
            cached = self._read(key, ExploreResponse)
            if cached is not None:
                return cached

        items, total = self.repo.list_stores(
            category=category,
            search=search,
            page=page,
            page_size=page_size,
        )
        result = ExploreResponse(
            items=[self._to_item(t) for t in items],
            total=total,
            page=page,
            page_size=page_size,
        )
        if key is not None:
            self._write(key, result)
        return result

    # ------------------------------------------------------------------
    # Caché (cache-aside; si Redis falla, se responde desde la base)
    # ------------------------------------------------------------------

    @staticmethod
    def _read(key: str, model):
        redis_client = get_redis()
        if redis_client is None:
            return None
        try:
            raw = redis_client.get(key)
            return model.model_validate_json(raw) if raw else None
        except Exception:
            logger.warning("Redis read failed for key=%s; falling through to DB", key, exc_info=True)
            return None

    @staticmethod
    def _write(key: str, value) -> None:
        redis_client = get_redis()
        if redis_client is None:
            return
        try:
            redis_client.set(key, value.model_dump_json(), ex=EXPLORE_CACHE_TTL)
        except Exception:
            logger.warning("Redis write failed for key=%s", key, exc_info=True)

    @staticmethod
    def _to_item(tenant) -> ExploreStoreItem:
        branding = read_branding(tenant.config)
        return ExploreStoreItem(
            slug=tenant.slug,
            business_name=tenant.business_name,
            business_type=tenant.business_type,
            logo_url=branding["logo_url"],
            banner_url=branding["banner_url"],
            tagline=read_profile(tenant.config)["tagline"],
        )
