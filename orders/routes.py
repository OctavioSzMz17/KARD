"""
Order endpoints — consumer JWT required on all routes.

POST  /api/v1/orders              → create order from cart
GET   /api/v1/orders              → list my orders (paginated)
GET   /api/v1/orders/{order_id}   → order detail
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from core.dependencies import CurrentConsumer, get_current_consumer, get_tenant_id
from db.database import get_db
from orders.models import (
    CreateOrderRequest,
    OrderListResponse,
    OrderResponse,
    SalesReportResponse,
)
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
    Valida stock del producto final y de sus insumos (receta), descuenta
    inventario en cascada, y vacía el carrito — todo atómico.

    Si algo no tiene stock:
      - on_insufficient_stock="reject" (default) → 409 con el detalle.
      - on_insufficient_stock="skip" → crea el pedido omitiendo lo faltante.
    """
    return OrderService(db).create_from_cart(
        consumer.consumer_id, payload.slug, payload.on_insufficient_stock
    )


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


# OJO: esta ruta va ANTES de "/{order_id}". Si se declara después, el path
# param la captura y "/sales" se interpreta como un order_id.
@router.get(
    "/business/sales",
    response_model=SalesReportResponse,
    summary="Sales report for the authenticated business (B2B)",
)
def list_business_sales(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    date_from: Optional[datetime] = Query(default=None, description="Inclusive, ISO-8601"),
    date_to: Optional[datetime] = Query(default=None, description="Exclusive, ISO-8601"),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None, description="Folio, nombre o correo del cliente"),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> SalesReportResponse:
    """
    Ventas del negocio autenticado, con los filtros del reporte.

    El tenant sale del JWT, nunca de la query — un negocio no puede pedir
    las ventas de otro (Rules.md section 2.6).
    """
    return OrderService(db).list_sales(
        tenant_id, page, page_size, date_from, date_to, status_filter, search
    )


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
