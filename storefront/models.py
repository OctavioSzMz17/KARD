"""
Pydantic response schemas for the public storefront (B2C layer).

These are intentionally *different* from the B2B `ProductResponse` —
they omit internal fields (tenant_id, created_by, is_deleted,
low_stock_threshold, inventory_count) that a public visitor must never see.
"""

from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel


class StorefrontImageResponse(BaseModel):
    image_id: str
    url: str
    alt_text: Optional[str] = None
    sort_order: int


class StorefrontProductResponse(BaseModel):
    """A single catalog item as seen by a public visitor."""

    product_id: str
    name: str
    description: Optional[str] = None
    price: Decimal
    product_type: str
    metadata: dict[str, Any]
    images: list[StorefrontImageResponse] = []


class StorefrontProductListResponse(BaseModel):
    """Paginated public catalog."""

    items: list[StorefrontProductResponse]
    total: int
    page: int
    page_size: int


class StorefrontTenantResponse(BaseModel):
    """Public business info shown at GET /s/{slug}."""

    slug: str
    business_name: str
    business_type: str
