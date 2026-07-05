"""
Public storefront routes — B2C, no authentication required.

All three endpoints resolve the tenant from the URL slug, not from
a JWT. A slug that doesn't exist or belongs to a non-public tenant
returns 404 — indistinguishable from "doesn't exist" (Rules.md §7.5).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from db.database import get_db
from storefront.models import StorefrontProductListResponse, StorefrontProductResponse, StorefrontTenantResponse
from storefront.service import StorefrontService

router = APIRouter()


@router.get(
    "/{slug}",
    response_model=StorefrontTenantResponse,
    summary="Public business info for a storefront",
)
def get_storefront(slug: str, db: Session = Depends(get_db)) -> StorefrontTenantResponse:
    return StorefrontService(db).get_storefront(slug)


@router.get(
    "/{slug}/products",
    response_model=StorefrontProductListResponse,
    summary="Paginated public product catalog (final products only)",
)
def list_products(
    slug: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
) -> StorefrontProductListResponse:
    return StorefrontService(db).list_products(slug, page, page_size, search)


@router.get(
    "/{slug}/products/{product_id}",
    response_model=StorefrontProductResponse,
    summary="Public detail for a single product",
)
def get_product(
    slug: str,
    product_id: str,
    db: Session = Depends(get_db),
) -> StorefrontProductResponse:
    return StorefrontService(db).get_product(slug, product_id)
