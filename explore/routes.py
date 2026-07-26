"""
Public discovery endpoints — no authentication required.

GET /api/v1/explore/categories  — business types with at least one public store
GET /api/v1/explore             — paginated list of public stores
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db.database import get_db
from explore.models import CategoriesResponse, ExploreResponse
from explore.service import ExploreService

router = APIRouter()


@router.get(
    "/categories",
    response_model=CategoriesResponse,
    summary="List business type categories that have at least one public store",
)
def get_categories(db: Session = Depends(get_db)) -> CategoriesResponse:
    """
    Returns the business types available in the marketplace.
    Only includes types with at least one active public storefront.
    The 'platform' type (internal SaaS tenant) is never exposed.
    """
    return ExploreService(db).get_categories()


@router.get(
    "",
    response_model=ExploreResponse,
    summary="Paginated discovery of public storefronts",
)
def list_stores(
    category: Optional[str] = Query(default=None, description="Filter by business_type (e.g. 'restaurant', 'optician')"),
    search: Optional[str] = Query(default=None, max_length=200, description="Partial match on business name"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ExploreResponse:
    """
    Discover public stores. All results are active + public tenants only.
    Combine category + search to narrow results.
    """
    return ExploreService(db).list_stores(
        category=category,
        search=search,
        page=page,
        page_size=page_size,
    )
