"""
Pydantic response schemas for the public storefront (B2C layer).

These are intentionally *different* from the B2B `ProductResponse` —
they omit internal fields (tenant_id, created_by, is_deleted,
low_stock_threshold, inventory_count) that a public visitor must never see.
"""

from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field


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


class StorefrontTheme(BaseModel):
    primary: Optional[str] = None
    accent: Optional[str] = None


class StorefrontHourEntry(BaseModel):
    days: Optional[str] = None
    hours: Optional[str] = None


class StorefrontSocial(BaseModel):
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    tiktok: Optional[str] = None
    x: Optional[str] = None
    youtube: Optional[str] = None


class StorefrontContact(BaseModel):
    """Always present in the response (with nulls) so the front never sees `undefined`."""

    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    maps_url: Optional[str] = None
    hours: list[StorefrontHourEntry] = Field(default_factory=list)
    social: StorefrontSocial = Field(default_factory=StorefrontSocial)


class StorefrontTenantResponse(BaseModel):
    """Public business info shown at GET /s/{slug}."""

    slug: str
    business_name: str
    business_type: str
    # branding (visual)
    logo_url: Optional[str] = None
    banner_url: Optional[str] = None
    theme: Optional[StorefrontTheme] = None
    # profile (editorial)
    tagline: Optional[str] = None
    description: Optional[str] = None
    contact: StorefrontContact = Field(default_factory=StorefrontContact)
