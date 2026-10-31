"""
Business logic for the public storefront (B2C layer).

Three invariants this service enforces:
  1. Only public, active tenants are reachable — all others look like 404.
  2. Only `product_type=final` products are exposed — `insumo` items are
     internal inventory and must never appear in a customer-facing catalog.
  3. Every catalog/product access is recorded in `consumer_events` so the
     Fase 3 AI engine can build behavioral profiles from day one.
"""

import logging
from typing import Optional

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from core.exceptions import StorefrontNotFoundError
from core.redis_client import (
    STOREFRONT_CACHE_TTL,
    get_redis,
    storefront_info_key,
    storefront_tenant_key,
)
from core.tenant_config import read_branding, read_fulfillment, read_profile
from images.repository import ImageRepository
from storefront.models import (
    StorefrontContact,
    StorefrontFulfillment,
    StorefrontHourEntry,
    StorefrontImageResponse,
    StorefrontProductListResponse,
    StorefrontProductResponse,
    StorefrontSocial,
    StorefrontTenantResponse,
    StorefrontTheme,
)
from storefront.repository import StorefrontRepository, record_event_detached

logger = logging.getLogger("kard.storefront")

PRODUCT_TYPE_PUBLIC = "final"


class StorefrontService:
    def __init__(self, db: Session, tasks: Optional[BackgroundTasks] = None):
        self.db = db
        self.repo = StorefrontRepository(db)
        self.image_repo = ImageRepository(db)
        # Sin `tasks` (otros llamadores, pruebas) los eventos se escriben en
        # línea, como antes; con `tasks` se registran tras responder.
        self.tasks = tasks

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    def get_storefront(self, slug: str) -> StorefrontTenantResponse:
        """
        Return public business info for the given slug.

        Served from Redis when possible: the business info only changes when
        its owner edits branding/profile/fulfillment, and every one of those
        edits invalidates this key.
        """
        redis_client = get_redis()
        info_key = storefront_info_key(slug)

        if redis_client is not None:
            try:
                cached = redis_client.get(info_key)
                if cached:
                    response = StorefrontTenantResponse.model_validate_json(cached)
                    tenant_id = self._cached_tenant_id(redis_client, slug)
                    if tenant_id:
                        self._track(tenant_id, "view_storefront", {"slug": slug})
                        return response
            except Exception:
                logger.warning("Redis read failed for key=%s; falling through to DB", info_key, exc_info=True)

        tenant = self._require_public_tenant(slug)
        self._remember_tenant_id(redis_client, slug, tenant.id)
        self._track(tenant.id, "view_storefront", {"slug": slug})

        branding = read_branding(tenant.config)
        theme = branding["theme"]
        profile = read_profile(tenant.config)

        response = StorefrontTenantResponse(
            slug=tenant.slug,
            business_name=tenant.business_name,
            business_type=tenant.business_type,
            logo_url=branding["logo_url"],
            banner_url=branding["banner_url"],
            theme=StorefrontTheme(primary=theme["primary"], accent=theme["accent"]),
            tagline=profile["tagline"],
            description=profile["description"],
            contact=self._to_public_contact(profile["contact"]),
            fulfillment=StorefrontFulfillment(**read_fulfillment(tenant.config)),
        )

        if redis_client is not None:
            try:
                redis_client.set(info_key, response.model_dump_json(), ex=STOREFRONT_CACHE_TTL)
            except Exception:
                logger.warning("Redis write failed for key=%s", info_key, exc_info=True)

        return response

    @staticmethod
    def _to_public_contact(contact: dict) -> StorefrontContact:
        social = contact["social"]
        return StorefrontContact(
            address=contact["address"],
            phone=contact["phone"],
            email=contact["email"],
            whatsapp=contact["whatsapp"],
            maps_url=contact["maps_url"],
            hours=[StorefrontHourEntry(days=h["days"], hours=h["hours"]) for h in contact["hours"]],
            social=StorefrontSocial(
                facebook=social["facebook"],
                instagram=social["instagram"],
                tiktok=social["tiktok"],
                x=social["x"],
                youtube=social["youtube"],
            ),
        )

    def list_products(
        self,
        slug: str,
        page: int,
        page_size: int,
        search: Optional[str] = None,
    ) -> StorefrontProductListResponse:
        """
        Return a paginated list of public-facing (final) products.

        Insumo products are always excluded — they are internal kitchen/
        workshop supplies, not items a customer can order.

        Results are cached in Redis (TTL 5 min) keyed by slug + pagination
        + search. The cache is invalidated whenever the tenant's B2B
        product list changes (create / update / delete).
        """
        tenant_id = self._tenant_id_for(slug)

        cache_key = f"storefront:catalog:{slug}:page:{page}:size:{page_size}:search:{search or ''}"
        redis_client = get_redis()

        # Cache read — bypass DB if we have a fresh result
        if redis_client is not None:
            try:
                cached = redis_client.get(cache_key)
                if cached:
                    logger.debug("Cache hit: %s", cache_key)
                    result = StorefrontProductListResponse.model_validate_json(cached)
                    self._track(tenant_id, "view_catalog", {"slug": slug, "page": page, "search": search, "cache": "hit"})
                    return result
            except Exception:
                logger.warning("Redis read failed for key=%s; falling through to DB", cache_key, exc_info=True)

        # Cache miss — query DB
        items, total = self.repo.products.list_products(
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
            search=search,
            product_type=PRODUCT_TYPE_PUBLIC,
        )

        # Las imágenes de toda la página en una sola consulta: pedirlas por
        # producto eran decenas de viajes a la base en cada fallo de caché.
        images_by_product = self.image_repo.list_for_owners(
            tenant_id, "product", [p.id for p in items]
        )

        result = StorefrontProductListResponse(
            items=[self._to_public_product(p, images_by_product.get(p.id, [])) for p in items],
            total=total,
            page=page,
            page_size=page_size,
        )

        # Cache write
        if redis_client is not None:
            try:
                redis_client.set(cache_key, result.model_dump_json(), ex=STOREFRONT_CACHE_TTL)
                logger.debug("Cache set (TTL=%ds): %s", STOREFRONT_CACHE_TTL, cache_key)
            except Exception:
                logger.warning("Redis write failed for key=%s", cache_key, exc_info=True)

        self._track(tenant_id, "view_catalog", {"slug": slug, "page": page, "search": search, "cache": "miss"})

        return result

    def get_product(self, slug: str, product_id: str) -> StorefrontProductResponse:
        """Return public detail for a single product."""
        tenant_id = self._tenant_id_for(slug)

        product = self.repo.products.get_by_id(tenant_id, product_id)
        if product is None or product.product_type != PRODUCT_TYPE_PUBLIC:
            raise StorefrontNotFoundError(f"Product '{product_id}' not found")

        self._track(tenant_id, "view_product", {"slug": slug, "product_id": product_id})

        images = self.image_repo.list_for_owner(tenant_id, "product", product.id)
        return self._to_public_product(product, images)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_public_tenant(self, slug: str):
        tenant = self.repo.get_public_tenant_by_slug(slug)
        if tenant is None:
            raise StorefrontNotFoundError(f"Storefront '{slug}' not found")
        return tenant

    def _tenant_id_for(self, slug: str) -> str:
        """
        slug → tenant_id, cache-aside.

        Resolving the business is the first query of every public request,
        and the answer (an id) almost never changes. A slug that does not
        resolve is NOT cached, so a tenant that is activated later becomes
        reachable immediately instead of after the TTL.
        """
        redis_client = get_redis()
        cached = self._cached_tenant_id(redis_client, slug)
        if cached:
            return cached
        tenant = self._require_public_tenant(slug)
        self._remember_tenant_id(redis_client, slug, tenant.id)
        return tenant.id

    @staticmethod
    def _cached_tenant_id(redis_client, slug: str) -> Optional[str]:
        if redis_client is None:
            return None
        try:
            return redis_client.get(storefront_tenant_key(slug)) or None
        except Exception:
            logger.warning("Redis read failed for tenant slug=%s", slug, exc_info=True)
            return None

    @staticmethod
    def _remember_tenant_id(redis_client, slug: str, tenant_id: str) -> None:
        if redis_client is None:
            return
        try:
            redis_client.set(storefront_tenant_key(slug), tenant_id, ex=STOREFRONT_CACHE_TTL)
        except Exception:
            logger.warning("Redis write failed for tenant slug=%s", slug, exc_info=True)

    def _track(self, tenant_id: str, event_type: str, payload: dict) -> None:
        """
        Record an analytics event without making the visitor wait for it.

        With `tasks` the write happens after the response is sent, on its own
        session; without them (other callers, tests) it is written inline.
        """
        if self.tasks is not None:
            self.tasks.add_task(record_event_detached, tenant_id, event_type, payload)
            return
        self.repo.record_event(tenant_id, event_type, payload)
        self.db.commit()

    @staticmethod
    def _to_public_product(product, raw_images) -> StorefrontProductResponse:
        images = [
            StorefrontImageResponse(
                image_id=img.id,
                url=img.url,
                alt_text=img.alt_text,
                sort_order=img.sort_order,
            )
            for img in raw_images
        ]
        return StorefrontProductResponse(
            product_id=product.id,
            name=product.name,
            description=product.description,
            price=product.price,
            product_type=product.product_type,
            metadata=product.metadata_,
            images=images,
        )
