"""Pydantic schemas for the orders module."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class CreateOrderRequest(BaseModel):
    slug: str
    on_insufficient_stock: Literal["reject", "skip"] = "reject"
    # "reject" (default): si algún producto/insumo no tiene stock → 409 con el
    #   detalle de lo faltante, no se crea nada.
    # "skip": crea el pedido omitiendo los productos no disponibles y los
    #   reporta en `skipped`.


class OrderItemResponse(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class SkippedItemResponse(BaseModel):
    """A cart item that was left out of the order due to insufficient stock."""

    product_id: str
    product_name: str
    reason: str  # product_shortage | ingredient_shortage | product_and_ingredient | unavailable


class OrderResponse(BaseModel):
    order_id: str
    slug: str
    business_name: str
    status: str
    total: Decimal
    items: list[OrderItemResponse]
    skipped: list[SkippedItemResponse] = []
    created_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    page: int
    page_size: int
