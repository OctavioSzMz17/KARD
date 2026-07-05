"""Pydantic schemas for the orders module."""

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel


class CreateOrderRequest(BaseModel):
    slug: str


class OrderItemResponse(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class OrderResponse(BaseModel):
    order_id: str
    slug: str
    business_name: str
    status: str
    total: Decimal
    items: list[OrderItemResponse]
    created_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    page: int
    page_size: int
