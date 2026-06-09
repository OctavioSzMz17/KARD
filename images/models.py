"""
Pydantic request/response schemas for the images module
(Rules.md sections 6 & 9 — validation at the entry point).

Images are URL-based: the caller supplies a URL pointing to wherever
the file is hosted (local path, Google Drive, S3, Cloudinary, etc.).
KARD stores the URL string and metadata — it never handles the binary
file itself in this iteration. File-upload support is a future feature.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ImageCreate(BaseModel):
    """Payload for `POST /products/{product_id}/images`."""

    url: str = Field(..., min_length=1, max_length=2000, description="URL of the image (local path or remote)")
    alt_text: Optional[str] = Field(default=None, max_length=255, description="Accessible description of the image")
    sort_order: int = Field(default=0, ge=0, description="Display order — lower value appears first (0 = primary)")


class ImageUpdate(BaseModel):
    """
    Payload for `PATCH /products/{product_id}/images/{image_id}`.

    All fields optional (PATCH semantics): only the keys present in the
    request body are updated; omitted keys are left unchanged.
    """

    url: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    alt_text: Optional[str] = Field(default=None, max_length=255)
    sort_order: Optional[int] = Field(default=None, ge=0)


class ImageResponse(BaseModel):
    """Returned by all image endpoints and embedded in ProductResponse."""

    image_id: str
    owner_type: str
    owner_id: str
    url: str
    alt_text: Optional[str] = None
    sort_order: int
    created_at: datetime
