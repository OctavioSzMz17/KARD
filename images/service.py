"""
Business logic for the images module (Rules.md sections 4 & 6).

`ImageService` is the only place that:
  - verifies the target product exists and belongs to the caller's tenant
    before touching any image row,
  - resolves `owner_type` / `owner_id` from verified route params (never
    from the request body — Rules.md section 7),
  - commits mutations and writes the audit trail (Rules.md section 11).

Routes stay thin (Rules.md section 5) and never touch the repositories
or `Session` directly.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from audit_logging.audit import log_operation
from core.dependencies import CurrentUser
from core.exceptions import ImageNotFoundError, ProductNotFoundError
from db.models import Image
from images.models import ImageCreate, ImageResponse, ImageUpdate
from images.repository import ImageRepository
from products.repository import ProductRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.images")

# The only owner_type currently in use. Additional types (e.g. "tenant",
# "category") can be added here later without touching the DB schema.
OWNER_TYPE_PRODUCT = "product"


class ImageService:
    def __init__(self, db: Session):
        self.db = db
        self.images = ImageRepository(db)
        self.products = ProductRepository(db)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def add_image(
        self, product_id: str, payload: ImageCreate, current_user: CurrentUser
    ) -> ImageResponse:
        """Attach a new image URL to a product (Rules.md — multi-tenant catalog)."""
        self._require_product(product_id, current_user.tenant_id)

        image = Image(
            id=generate_id("img"),
            tenant_id=current_user.tenant_id,
            owner_type=OWNER_TYPE_PRODUCT,
            owner_id=product_id,
            url=payload.url,
            alt_text=payload.alt_text,
            sort_order=payload.sort_order,
        )
        self.images.create(image)
        self.db.commit()

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="ADD_IMAGE",
            resource_id=image.id,
            after={"owner_type": OWNER_TYPE_PRODUCT, "owner_id": product_id, "url": image.url},
        )
        return self._to_response(image)

    def list_images(
        self, product_id: str, current_user: CurrentUser
    ) -> list[ImageResponse]:
        """List all images for a product, ordered by sort_order."""
        self._require_product(product_id, current_user.tenant_id)
        images = self.images.list_for_owner(
            current_user.tenant_id, OWNER_TYPE_PRODUCT, product_id
        )
        return [self._to_response(img) for img in images]

    def update_image(
        self,
        product_id: str,
        image_id: str,
        payload: ImageUpdate,
        current_user: CurrentUser,
    ) -> ImageResponse:
        """Partial update of an image's url, alt_text, or sort_order."""
        self._require_product(product_id, current_user.tenant_id)
        image = self._require_image(image_id, product_id, current_user.tenant_id)
        before = {"url": image.url, "alt_text": image.alt_text, "sort_order": image.sort_order}

        if payload.url is not None:
            image.url = payload.url
        if payload.alt_text is not None:
            image.alt_text = payload.alt_text
        if payload.sort_order is not None:
            image.sort_order = payload.sort_order

        self.images.save(image)
        self.db.commit()

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="UPDATE_IMAGE",
            resource_id=image.id,
            before=before,
            after={"url": image.url, "alt_text": image.alt_text, "sort_order": image.sort_order},
        )
        return self._to_response(image)

    def delete_image(
        self, product_id: str, image_id: str, current_user: CurrentUser
    ) -> None:
        """Hard-delete an image (assets have no soft-delete; audit trail is enough)."""
        self._require_product(product_id, current_user.tenant_id)
        image = self._require_image(image_id, product_id, current_user.tenant_id)
        before = {"url": image.url, "alt_text": image.alt_text, "sort_order": image.sort_order}

        self.images.delete(image)
        self.db.commit()

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="DELETE_IMAGE",
            resource_id=image_id,
            before=before,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_product(self, product_id: str, tenant_id: str) -> None:
        """
        Verify the product exists and belongs to this tenant before
        touching its images — prevents cross-tenant image injection.
        """
        if self.products.get_by_id(tenant_id, product_id) is None:
            raise ProductNotFoundError(product_id)

    def _require_image(
        self, image_id: str, product_id: str, tenant_id: str
    ) -> Image:
        """
        Fetch the image, verifying it belongs to the tenant *and* to the
        specific product in the URL. An image from a different product
        (same tenant) should be as invisible as one from another tenant.
        """
        image = self.images.get_by_id(tenant_id, image_id)
        if image is None or image.owner_id != product_id:
            raise ImageNotFoundError(image_id)
        return image

    @staticmethod
    def _to_response(image: Image) -> ImageResponse:
        return ImageResponse(
            image_id=image.id,
            owner_type=image.owner_type,
            owner_id=image.owner_id,
            url=image.url,
            alt_text=image.alt_text,
            sort_order=image.sort_order,
            created_at=image.created_at,
        )
