"""
Product image endpoints (Rules.md section 5 — routes stay thin):

  POST   /api/v1/products/{product_id}/images                    — add image (201)
  GET    /api/v1/products/{product_id}/images                    — list images (200)
  PATCH  /api/v1/products/{product_id}/images/{image_id}         — update image (200)
  DELETE /api/v1/products/{product_id}/images/{image_id}         — delete image (204)

Routes only: parse/validate the request, resolve DB session and verified
caller identity, delegate to `ImageService`, return its result.
`tenant_id` always comes from `current_user` (verified JWT) — never from
the request body or path (Rules.md section 7).
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core.dependencies import CurrentUser, require_permission
from core import permissions as P
from db.database import get_db
from images.models import ImageCreate, ImageResponse, ImageUpdate
from images.service import ImageService

router = APIRouter()


@router.post(
    "/{product_id}/images",
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an image to a product",
)
def add_image(
    product_id: str,
    payload: ImageCreate,
    current_user: CurrentUser = Depends(require_permission(P.PRODUCTS_WRITE)),
    db: Session = Depends(get_db),
) -> ImageResponse:
    return ImageService(db).add_image(product_id, payload, current_user)


@router.get(
    "/{product_id}/images",
    response_model=list[ImageResponse],
    summary="List all images for a product, ordered by sort_order",
)
def list_images(
    product_id: str,
    current_user: CurrentUser = Depends(require_permission(P.PRODUCTS_READ)),
    db: Session = Depends(get_db),
) -> list[ImageResponse]:
    return ImageService(db).list_images(product_id, current_user)


@router.patch(
    "/{product_id}/images/{image_id}",
    response_model=ImageResponse,
    summary="Partially update an image (url / alt_text / sort_order)",
)
def update_image(
    product_id: str,
    image_id: str,
    payload: ImageUpdate,
    current_user: CurrentUser = Depends(require_permission(P.PRODUCTS_WRITE)),
    db: Session = Depends(get_db),
) -> ImageResponse:
    return ImageService(db).update_image(product_id, image_id, payload, current_user)


@router.delete(
    "/{product_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an image (hard delete — assets have no soft-delete)",
)
def delete_image(
    product_id: str,
    image_id: str,
    current_user: CurrentUser = Depends(require_permission(P.PRODUCTS_WRITE)),
    db: Session = Depends(get_db),
) -> None:
    ImageService(db).delete_image(product_id, image_id, current_user)
