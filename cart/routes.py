"""
Cart endpoints — consumer JWT required on all routes.

POST   /api/v1/cart/{slug}/items          → add / update item (qty=0 removes)
GET    /api/v1/cart/{slug}                → view current cart
DELETE /api/v1/cart/{slug}/items/{pid}    → remove one item
DELETE /api/v1/cart/{slug}               → clear entire cart
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from cart.models import AddToCartRequest, CartResponse
from cart.service import CartService
from core.dependencies import CurrentConsumer, get_current_consumer
from db.database import get_db

router = APIRouter()


@router.post(
    "/{slug}/items",
    response_model=CartResponse,
    summary="Add or update a product in the cart (quantity=0 removes it)",
)
def add_item(
    slug: str,
    payload: AddToCartRequest,
    consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> CartResponse:
    return CartService(db).add_item(consumer.consumer_id, slug, payload)


@router.get(
    "/{slug}",
    response_model=CartResponse,
    summary="View current cart for a storefront",
)
def get_cart(
    slug: str,
    consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> CartResponse:
    return CartService(db).get_cart(consumer.consumer_id, slug)


@router.delete(
    "/{slug}/items/{product_id}",
    response_model=CartResponse,
    summary="Remove a single item from the cart",
)
def remove_item(
    slug: str,
    product_id: str,
    consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> CartResponse:
    return CartService(db).remove_item(consumer.consumer_id, slug, product_id)


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear the entire cart for a storefront",
)
def clear_cart(
    slug: str,
    consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> None:
    CartService(db).clear(consumer.consumer_id, slug)
