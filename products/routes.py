"""
Product catalog & inventory endpoints (Rules.md section 5 — routes stay thin):

  POST   /api/v1/products                       — create (201)
  GET    /api/v1/products                       — list, paginated + searchable (200)
  GET    /api/v1/products/low-stock             — low-stock alert widget (200)
  GET    /api/v1/products/{product_id}          — get one (200)
  PATCH  /api/v1/products/{product_id}          — partial update (200)
  DELETE /api/v1/products/{product_id}          — soft delete (204)
  POST   /api/v1/products/{product_id}/sell     — decrease stock (200)
  POST   /api/v1/products/{product_id}/restock  — increase stock (200)

Routes only: parse/validate the request, resolve the DB session and
verified caller identity, delegate to `ProductService`, and return its
result. `tenant_id` always comes from `current_user` (the verified
JWT) — never from the request body/path/query (Rules.md section 7).

NOTE: `/low-stock` is declared *before* `/{product_id}` — FastAPI
matches routes in registration order, and a path parameter would
otherwise swallow the literal "low-stock" segment.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from core.dependencies import CurrentUser, get_current_user
from db.database import get_db
from products.models import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
    StockChangeRequest,
    StockChangeResponse,
)
from products.service import ProductService

router = APIRouter()


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product in the caller's tenant catalog",
)
def create_product(
    payload: ProductCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductResponse:
    return ProductService(db).create_product(payload, current_user)


@router.get(
    "",
    response_model=ProductListResponse,
    summary="List the caller's tenant products (paginated, searchable)",
)
def list_products(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None, max_length=500, description="Filter by name (case-insensitive)"),
    product_type: Optional[str] = Query(default=None, description="Filter by product_type: insumo | final"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductListResponse:
    return ProductService(db).list_products(
        current_user, page=page, page_size=page_size, search=search, product_type=product_type
    )


@router.get(
    "/low-stock",
    response_model=list[ProductResponse],
    summary="Products whose stock has fallen below their alert threshold",
)
def low_stock(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ProductResponse]:
    return ProductService(db).get_low_stock(current_user)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Get a single product by id",
)
def get_product(
    product_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductResponse:
    return ProductService(db).get_product(product_id, current_user)


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    summary="Partially update a product (name/description/price/threshold/metadata)",
)
def update_product(
    product_id: str,
    payload: ProductUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductResponse:
    return ProductService(db).update_product(product_id, payload, current_user)


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a product (is_deleted = true; never a hard delete)",
)
def delete_product(
    product_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    ProductService(db).soft_delete_product(product_id, current_user)


@router.post(
    "/{product_id}/sell",
    response_model=StockChangeResponse,
    summary="Decrease stock by `quantity` (never below zero)",
)
def sell_product(
    product_id: str,
    payload: StockChangeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StockChangeResponse:
    return ProductService(db).sell_product(product_id, payload, current_user)


@router.post(
    "/{product_id}/restock",
    response_model=StockChangeResponse,
    summary="Increase stock by `quantity` (e.g. a new shipment arrived)",
)
def restock_product(
    product_id: str,
    payload: StockChangeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StockChangeResponse:
    return ProductService(db).restock_product(product_id, payload, current_user)
