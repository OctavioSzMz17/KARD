"""
Public storefront routes — B2C, no authentication required.

All three endpoints resolve the tenant from the URL slug, not from
a JWT. A slug that doesn't exist or belongs to a non-public tenant
returns 404 — indistinguishable from "doesn't exist" (Rules.md §7.5).

These responses are the same for every visitor, so they carry HTTP cache
headers: a browser (or a CDN) can reuse them for a minute without asking,
and afterwards revalidate with `If-None-Match` for a body-less 304.
"""

import hashlib
from typing import Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.database import get_db
from storefront.models import StorefrontProductListResponse, StorefrontProductResponse, StorefrontTenantResponse
from storefront.service import StorefrontService

router = APIRouter()

# `public`: nothing here depends on who asks. `stale-while-revalidate` lets
# the browser show the old copy instantly while it refreshes it in the
# background, so a visitor never waits on a revalidation.
PUBLIC_CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=300"


def _with_http_cache(request: Request, response: Response, model: BaseModel) -> Union[BaseModel, Response]:
    """
    Attach `ETag` + `Cache-Control`, or answer 304 when the client's copy is current.

    The ETag is a hash of the serialized response, so it changes exactly
    when the content does — including after a Redis invalidation that
    rebuilds identical data (same hash, still a 304).
    """
    etag = 'W/"' + hashlib.sha1(model.model_dump_json().encode("utf-8")).hexdigest()[:24] + '"'
    headers = {"ETag": etag, "Cache-Control": PUBLIC_CACHE_CONTROL}

    sent = [t.strip() for t in request.headers.get("if-none-match", "").split(",") if t.strip()]
    if etag in sent or "*" in sent:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    response.headers.update(headers)
    return model


@router.get(
    "/{slug}",
    response_model=StorefrontTenantResponse,
    summary="Public business info for a storefront",
)
def get_storefront(
    slug: str,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    info = StorefrontService(db, background_tasks).get_storefront(slug)
    return _with_http_cache(request, response, info)


@router.get(
    "/{slug}/products",
    response_model=StorefrontProductListResponse,
    summary="Paginated public product catalog (final products only)",
)
def list_products(
    slug: str,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
):
    catalog = StorefrontService(db, background_tasks).list_products(slug, page, page_size, search)
    return _with_http_cache(request, response, catalog)


@router.get(
    "/{slug}/products/{product_id}",
    response_model=StorefrontProductResponse,
    summary="Public detail for a single product",
)
def get_product(
    slug: str,
    product_id: str,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    product = StorefrontService(db, background_tasks).get_product(slug, product_id)
    return _with_http_cache(request, response, product)
