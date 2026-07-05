"""
Data-access layer for the explore / discovery module.

All queries are restricted to tenants that are:
  - is_public = TRUE  (opted in to the marketplace)
  - is_active = TRUE  (not suspended)
  - business_type != 'platform' (never expose the internal SaaS tenant)
"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Tenant


# Tenant types that are internal and must never appear in discovery
_HIDDEN_TYPES = {"platform"}


class ExploreRepository:
    def __init__(self, db: Session):
        self.db = db

    def _base_query(self):
        # slug IS NOT NULL: a public tenant without a slug is unreachable via
        # /s/{slug} anyway — listing it would cause a 500 on serialization.
        return (
            self.db.query(Tenant)
            .filter(
                Tenant.is_public.is_(True),
                Tenant.is_active.is_(True),
                Tenant.business_type.notin_(_HIDDEN_TYPES),
                Tenant.slug.isnot(None),
            )
        )

    def get_categories(self) -> list[dict]:
        """
        Return each business_type that has at least one public active store,
        with its store count. Ordered by count descending so the most populated
        category appears first.
        """
        rows = (
            self.db.query(Tenant.business_type, func.count(Tenant.id).label("store_count"))
            .filter(
                Tenant.is_public.is_(True),
                Tenant.is_active.is_(True),
                Tenant.business_type.notin_(_HIDDEN_TYPES),
                Tenant.slug.isnot(None),
            )
            .group_by(Tenant.business_type)
            .order_by(func.count(Tenant.id).desc())
            .all()
        )
        return [{"business_type": r.business_type, "store_count": r.store_count} for r in rows]

    def list_stores(
        self,
        category: Optional[str],
        search: Optional[str],
        page: int,
        page_size: int,
    ) -> tuple[list[Tenant], int]:
        """
        Return (items, total) for public stores matching the filters.

        - category: exact match on business_type (optional)
        - search: case-insensitive partial match on business_name (optional)
        - Ordered alphabetically so results are stable across pages.
        """
        q = self._base_query()

        if category:
            q = q.filter(Tenant.business_type == category)

        if search:
            q = q.filter(Tenant.business_name.ilike(f"%{search}%"))

        total = q.count()
        items = (
            q.order_by(Tenant.business_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total
