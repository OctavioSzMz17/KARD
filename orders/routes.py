"""
Endpoints de ventas, para los dos canales del producto.

Cliente (JWT de consumer):
  POST  /api/v1/orders              → convierte el carrito en pedido
  GET   /api/v1/orders              → mis pedidos (paginado)
  GET   /api/v1/orders/{order_id}   → detalle

Negocio (JWT de usuario):
  POST  /api/v1/orders/business/sales                      → venta de mostrador
  GET   /api/v1/orders/business/sales                      → reporte de ventas
  PATCH /api/v1/orders/business/sales/{order_id}/status    → mover pago o entrega
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from core.dependencies import (
    CurrentConsumer,
    CurrentUser,
    get_current_consumer,
    get_current_user,
    get_tenant_id,
)
from db.database import get_db
from orders.models import (
    CreateOrderRequest,
    CreatePosSaleRequest,
    OrderListResponse,
    OrderResponse,
    SaleResponse,
    SalesReportResponse,
    UpdateSaleStatusRequest,
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
    return OrderService(db).create_from_cart(consumer.consumer_id, payload)


@router.post(
    "/business/sales",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a counter sale from the POS (B2B)",
)
def create_pos_sale(
    payload: CreatePosSaleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrderResponse:
    """
    Venta capturada por un empleado en el mostrador.

    A diferencia del checkout del cliente, los artículos llegan en el cuerpo:
    no hay carrito en Redis porque no hay cuenta de cliente detrás. El tenant
    y el empleado salen del token, nunca del request.

    Reusa el mismo motor que el checkout B2C — bloqueo de filas, descuento en
    cascada de insumos y bitácora atómica — así que una venta de mostrador
    mueve el inventario exactamente igual que una en línea.
    """
    return OrderService(db).create_pos_sale(
        current_user.tenant_id, current_user.user_id, payload
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


@router.patch(
    "/business/sales/{order_id}/status",
    response_model=SaleResponse,
    summary="Update the status of one of the business's sales (B2B)",
)
def update_sale_status(
    order_id: str,
    payload: UpdateSaleStatusRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
) -> SaleResponse:
    """
    Un negocio solo puede mover pedidos propios; los ajenos dan 404.

    El pago y la entrega se mueven por separado — o juntos en una llamada —
    porque son ejes independientes: cobrar una mesa no la sirve.
    """
    return OrderService(db).update_sale_status(
        tenant_id, order_id, payload.status, payload.fulfillment_status
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
