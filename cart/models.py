"""Pydantic schemas for the cart module."""

from decimal import Decimal
from pydantic import BaseModel, Field


class AddToCartRequest(BaseModel):
    product_id: str
    quantity: int = Field(..., ge=0, le=100, description="0 = remove item from cart")


class CartItemResponse(BaseModel):
    product_id: str
    name: str
    quantity: int
    price_snapshot: Decimal
    subtotal: Decimal


class CartResponse(BaseModel):
    slug: str
    business_name: str
    items: list[CartItemResponse]
    total: Decimal
    item_count: int
