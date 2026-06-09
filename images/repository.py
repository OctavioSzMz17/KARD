"""
Data-access layer for the images module (Rules.md section 3 —
Repository Pattern).

Pure query building — no business rules, no commits.
Every query here filters by `tenant_id` (Rules.md section 7):
an image row owned by another tenant must be as invisible as one
that doesn't exist.
"""

from typing import Optional

from sqlalchemy.orm import Session

from db.models import Image


class ImageRepository:
    """Read/write access to the `images` table, always tenant-scoped."""

    def __init__(self, db: Session):
        self.db = db

    def list_for_owner(self, tenant_id: str, owner_type: str, owner_id: str) -> list[Image]:
        """
        All images belonging to one owner, ordered by `sort_order` ASC
        then `created_at` ASC (stable tie-break).
        """
        return (
            self.db.query(Image)
            .filter(
                Image.tenant_id == tenant_id,
                Image.owner_type == owner_type,
                Image.owner_id == owner_id,
            )
            .order_by(Image.sort_order.asc(), Image.created_at.asc())
            .all()
        )

    def get_by_id(self, tenant_id: str, image_id: str) -> Optional[Image]:
        """
        Fetch a single image by id, scoped to the caller's tenant.

        Cross-tenant lookups return `None` (-> 404) rather than 403 —
        we never confirm whether a foreign-tenant resource exists
        (Rules.md section 7.5).
        """
        return (
            self.db.query(Image)
            .filter(Image.tenant_id == tenant_id, Image.id == image_id)
            .first()
        )

    def create(self, image: Image) -> Image:
        self.db.add(image)
        self.db.flush()
        return image

    def save(self, image: Image) -> Image:
        """Persist in-place mutations made to an already-tracked `Image` instance."""
        self.db.add(image)
        self.db.flush()
        return image

    def delete(self, image: Image) -> None:
        """Hard-delete an image row (images have no soft-delete — they are pure assets)."""
        self.db.delete(image)
        self.db.flush()
