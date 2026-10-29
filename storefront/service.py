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

from sqlalchemy.orm import Session

from core.exceptions import StorefrontNotFoundError
from core.redis_client import STOREFRONT_CACHE_TTL, get_redis
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
from storefront.repository import StorefrontRepository

logger = logging.getLogger("kard.storefront")

PRODUCT_TYPE_PUBLIC = "final"


class StorefrontService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = StorefrontRepository(db)
        self.image_repo = ImageRepository(db)

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    def get_storefront(self, slug: str) -> StorefrontTenantResponse:
        """Return public business info for the given slug."""
        tenant = self._require_public_tenant(slug)

        self.repo.record_event(tenant.id, "view_storefront", {"slug": slug})
        self.db.commit()

        branding = read_branding(tenant.config)
        theme = branding["theme"]
        profile = read_profile(tenant.config)

        return StorefrontTenantResponse(
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
        tenant = self._require_public_tenant(slug)

        cache_key = f"storefront:catalog:{slug}:page:{page}:size:{page_size}:search:{search or ''}"
        redis_client = get_redis()

        # Cache read — bypass DB if we have a fresh result
        if redis_client is not None:
            try:
                cached = redis_client.get(cache_key)
                if cached:
                    logger.debug("Cache hit: %s", cache_key)
                    self.repo.record_event(tenant.id, "view_catalog", {"slug": slug, "page": page, "search": search, "cache": "hit"})
                    self.db.commit()
                    return StorefrontProductListResponse.model_validate_json(cached)
            except Exception:
                logger.warning("Redis read failed for key=%s; falling through to DB", cache_key, exc_info=True)

        # Cache miss — query DB
        items, total = self.repo.products.list_products(
            tenant_id=tenant.id,
            page=page,
            page_size=page_size,
            search=search,
            product_type=PRODUCT_TYPE_PUBLIC,
        )

        result = StorefrontProductListResponse(
            items=[self._to_public_product(p, tenant.id) for p in items],
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

        self.repo.record_event(tenant.id, "view_catalog", {"slug": slug, "page": page, "search": search, "cache": "miss"})
        self.db.commit()

        return result

    def get_product(self, slug: str, product_id: str) -> StorefrontProductResponse:
        """Return public detail for a single product."""
        tenant = self._require_public_tenant(slug)

        product = self.repo.products.get_by_id(tenant.id, product_id)
        if product is None or product.product_type != PRODUCT_TYPE_PUBLIC:
            raise StorefrontNotFoundError(f"Product '{product_id}' not found")

        self.repo.record_event(
            tenant.id,
            "view_product",
            {"slug": slug, "product_id": product_id},
        )
        self.db.commit()

        return self._to_public_product(product, tenant.id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_public_tenant(self, slug: str):
        tenant = self.repo.get_public_tenant_by_slug(slug)
        if tenant is None:
            raise StorefrontNotFoundError(f"Storefront '{slug}' not found")
        return tenant

    def _to_public_product(self, product, tenant_id: str) -> StorefrontProductResponse:
        raw_images = self.image_repo.list_for_owner(tenant_id, "product", product.id)
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
