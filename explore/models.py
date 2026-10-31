"""
Pydantic schemas for the explore / discovery module.

These are public-facing — never expose internal tenant fields.
"""

from typing import Optional

from pydantic import BaseModel


class CategoryItem(BaseModel):
    """A business type that has at least one active public storefront."""

    business_type: str
    store_count: int


class CategoriesResponse(BaseModel):
    categories: list[CategoryItem]


class ExploreStoreItem(BaseModel):
    """A single public store as it appears in the discovery listing."""

    slug: str
    business_name: str
    business_type: str
    logo_url: Optional[str] = None
    # Portada de la tarjeta: el logo es un logotipo (suele traer texto) y recortado
    # como foto se ve mal; el banner sí es una imagen pensada para cubrir.
    banner_url: Optional[str] = None
    tagline: Optional[str] = None


class ExploreResponse(BaseModel):
    """Paginated list of public stores."""

    items: list[ExploreStoreItem]
    total: int
    page: int
    page_size: int
