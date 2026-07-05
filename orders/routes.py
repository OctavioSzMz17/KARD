"""
Order endpoints — consumer JWT required on all routes.

POST  /api/v1/orders              → create order from cart
GET   /api/v1/orders              → list my orders (paginated)
GET   /api/v1/orders/{order_id}   → order detail
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from core.dependencies import CurrentConsumer, get_current_consumer
from db.database import get_db
from orders.models import CreateOrderRequest, OrderListResponse, OrderResponse
from orders.service import OrderService

router = APIRouter()


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an order from the current cart",
)
def create_order(
    payload: CreateOrderRequest,
    consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> OrderResponse:
    """
    Converts the consumer's cart for `slug` into a confirmed order.
    Validates stock, decrements inventory, and clears the cart atomically.
    Returns the created order with status 'pending'.
    """
    return OrderService(db).create_from_cart(consumer.consumer_id, payload.slug)


@router.get(
    "",
    response_model=OrderListResponse,
    summary="List my orders across all storefronts",
)
def list_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> OrderListResponse:
    return OrderService(db).list_orders(consumer.consumer_id, page, page_size)


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    summary="Get order detail",
)
def get_order(
    order_id: str,
    consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> OrderResponse:
    """A consumer can only see their own orders — others return 404."""
    return OrderService(db).get_order(consumer.consumer_id, order_id)
