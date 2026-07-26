"""
Recipe (Bill of Materials) endpoints — B2B, business auth required.
Mounted under /api/v1/products so recipes live alongside their product.

  GET    /api/v1/products/{product_id}/recipe                    — view recipe
  POST   /api/v1/products/{product_id}/recipe/items              — add/update ingredient
  DELETE /api/v1/products/{product_id}/recipe/items/{insumo_id}  — remove ingredient

All three return the full, updated recipe so the frontend always has the
current state after a mutation.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.dependencies import CurrentUser, get_current_user
from db.database import get_db
from recipes.models import RecipeItemCreate, RecipeResponse
from recipes.service import RecipeService

router = APIRouter()


@router.get(
    "/{product_id}/recipe",
    response_model=RecipeResponse,
    summary="View the recipe (ingredients) of a final product",
)
def get_recipe(
    product_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecipeResponse:
    return RecipeService(db).get_recipe(product_id, current_user)


@router.post(
    "/{product_id}/recipe/items",
    response_model=RecipeResponse,
    summary="Add an ingredient to the recipe (or update its quantity)",
)
def add_recipe_item(
    product_id: str,
    payload: RecipeItemCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecipeResponse:
    return RecipeService(db).add_item(product_id, payload, current_user)


@router.delete(
    "/{product_id}/recipe/items/{insumo_id}",
    response_model=RecipeResponse,
    summary="Remove an ingredient from the recipe",
)
def remove_recipe_item(
    product_id: str,
    insumo_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecipeResponse:
    return RecipeService(db).remove_item(product_id, insumo_id, current_user)
