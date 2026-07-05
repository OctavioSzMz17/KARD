"""
Pydantic schemas for the explore / discovery module.

These are public-facing — never expose internal tenant fields.
"""

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


class ExploreResponse(BaseModel):
    """Paginated list of public stores."""

    items: list[ExploreStoreItem]
    total: int
    page: int
    page_size: int
