"""
Business logic for the explore / discovery module.
"""

from typing import Optional

from sqlalchemy.orm import Session

from core.tenant_config import read_branding, read_profile
from explore.models import CategoriesResponse, CategoryItem, ExploreResponse, ExploreStoreItem
from explore.repository import ExploreRepository


class ExploreService:
    def __init__(self, db: Session):
        self.repo = ExploreRepository(db)

    def get_categories(self) -> CategoriesResponse:
        """Return business types that have at least one active public storefront."""
        rows = self.repo.get_categories()
        return CategoriesResponse(
            categories=[
                CategoryItem(business_type=r["business_type"], store_count=r["store_count"])
                for r in rows
            ]
        )

    def list_stores(
        self,
        category: Optional[str],
        search: Optional[str],
        page: int,
        page_size: int,
    ) -> ExploreResponse:
        """Paginated public store listing with optional category and search filters."""
        items, total = self.repo.list_stores(
            category=category,
            search=search,
            page=page,
            page_size=page_size,
        )
        return ExploreResponse(
            items=[
                self._to_item(t)
                for t in items
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

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
