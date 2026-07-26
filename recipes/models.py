"""Pydantic schemas for the recipes (Bill of Materials) module."""

from typing import Optional

from pydantic import BaseModel, Field


class RecipeItemCreate(BaseModel):
    """Payload to add/update an ingredient in a final product's recipe."""

    insumo_product_id: str
    quantity: int = Field(
        ...,
        gt=0,
        le=100_000,
        description="Unidades del insumo consumidas por cada unidad del producto final",
    )


class RecipeItemResponse(BaseModel):
    insumo_id: str
    insumo_name: str
    quantity: int
    unit: Optional[str] = None  # unidad_medida del insumo (kg, pieza, litro, ...)


class RecipeResponse(BaseModel):
    final_product_id: str
    final_product_name: str
    items: list[RecipeItemResponse]
