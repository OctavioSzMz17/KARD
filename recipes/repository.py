"""Data-access layer for the recipes module. Always tenant-scoped."""

from typing import Optional

from sqlalchemy.orm import Session

from db.models import ProductRecipe


class RecipeRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_for_final(self, tenant_id: str, final_product_id: str) -> list[ProductRecipe]:
        return (
            self.db.query(ProductRecipe)
            .filter(
                ProductRecipe.tenant_id == tenant_id,
                ProductRecipe.final_product_id == final_product_id,
            )
            .all()
        )

    def get_item(self, tenant_id: str, final_product_id: str, insumo_product_id: str) -> Optional[ProductRecipe]:
        return (
            self.db.query(ProductRecipe)
            .filter(
                ProductRecipe.tenant_id == tenant_id,
                ProductRecipe.final_product_id == final_product_id,
                ProductRecipe.insumo_product_id == insumo_product_id,
            )
            .first()
        )

    def add(self, recipe: ProductRecipe) -> ProductRecipe:
        self.db.add(recipe)
        self.db.flush()
        return recipe

    def delete(self, recipe: ProductRecipe) -> None:
        self.db.delete(recipe)
        self.db.flush()
