"""
Recipe (Bill of Materials) business logic.

A recipe links a *final* product to the *insumos* it consumes when sold.
All operations are tenant-scoped via the verified JWT (Rules.md §7).
"""

import logging

from sqlalchemy.orm import Session

from core.dependencies import CurrentUser
from core.exceptions import InvalidRecipeError, ProductNotFoundError
from db.models import ProductRecipe
from products.repository import ProductRepository
from recipes.models import RecipeItemCreate, RecipeItemResponse, RecipeResponse
from recipes.repository import RecipeRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.recipes")


class RecipeService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = RecipeRepository(db)
        self.products = ProductRepository(db)

    def _require_final(self, tenant_id: str, product_id: str):
        product = self.products.get_by_id(tenant_id, product_id)
        if product is None:
            raise ProductNotFoundError(product_id)
        if product.product_type != "final":
            raise InvalidRecipeError("Solo los productos de tipo 'final' pueden tener una receta")
        return product

    def add_item(self, product_id: str, payload: RecipeItemCreate, current_user: CurrentUser) -> RecipeResponse:
        """Add an ingredient to a final product's recipe (or update its quantity)."""
        tenant_id = current_user.tenant_id
        final = self._require_final(tenant_id, product_id)

        if payload.insumo_product_id == product_id:
            raise InvalidRecipeError("Un producto no puede ser ingrediente de sí mismo")

        insumo = self.products.get_by_id(tenant_id, payload.insumo_product_id)
        if insumo is None:
            raise ProductNotFoundError(payload.insumo_product_id)
        if insumo.product_type != "insumo":
            raise InvalidRecipeError("El ingrediente debe ser un producto de tipo 'insumo'")

        existing = self.repo.get_item(tenant_id, product_id, payload.insumo_product_id)
        if existing is not None:
            existing.quantity = payload.quantity  # upsert: update the quantity
        else:
            self.repo.add(ProductRecipe(
                id=generate_id("rec", 20),
                tenant_id=tenant_id,
                final_product_id=product_id,
                insumo_product_id=payload.insumo_product_id,
                quantity=payload.quantity,
            ))

        self.db.commit()
        logger.info("Recipe item set: final=%s insumo=%s qty=%s",
                    product_id, payload.insumo_product_id, payload.quantity)
        return self._build_recipe(tenant_id, final.id, final.name)

    def get_recipe(self, product_id: str, current_user: CurrentUser) -> RecipeResponse:
        final = self._require_final(current_user.tenant_id, product_id)
        return self._build_recipe(current_user.tenant_id, final.id, final.name)

    def remove_item(self, product_id: str, insumo_id: str, current_user: CurrentUser) -> RecipeResponse:
        tenant_id = current_user.tenant_id
        final = self._require_final(tenant_id, product_id)

        item = self.repo.get_item(tenant_id, product_id, insumo_id)
        if item is None:
            raise ProductNotFoundError(insumo_id)  # not in this recipe → 404
        self.repo.delete(item)
        self.db.commit()
        logger.info("Recipe item removed: final=%s insumo=%s", product_id, insumo_id)
        return self._build_recipe(tenant_id, final.id, final.name)

    def _build_recipe(self, tenant_id: str, final_id: str, final_name: str) -> RecipeResponse:
        rows = self.repo.list_for_final(tenant_id, final_id)
        items: list[RecipeItemResponse] = []
        for row in rows:
            insumo = self.products.get_by_id(tenant_id, row.insumo_product_id)
            name = insumo.name if insumo is not None else "(insumo no disponible)"
            unit = None
            if insumo is not None and isinstance(insumo.metadata_, dict):
                unit = insumo.metadata_.get("unidad_medida")
            items.append(RecipeItemResponse(
                insumo_id=row.insumo_product_id,
                insumo_name=name,
                quantity=row.quantity,
                unit=unit,
            ))
        return RecipeResponse(final_product_id=final_id, final_product_name=final_name, items=items)
