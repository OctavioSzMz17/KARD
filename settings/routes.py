"""
Tenant settings endpoints.

  GET /api/v1/settings/branding  — logo, banner, theme
  PUT /api/v1/settings/branding
  GET /api/v1/settings/profile   — tagline, description, contact
  PUT /api/v1/settings/profile

All require 🔒 business JWT. The tenant is always taken from the token
(never from a parameter) — multi-tenant isolation (Rules.md §7).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.dependencies import CurrentUser, get_current_user
from db.database import get_db
from settings.models import BrandingResponse, BrandingUpdate, ProfileResponse, ProfileUpdate
from settings.service import SettingsService

router = APIRouter()


@router.get(
    "/branding",
    response_model=BrandingResponse,
    summary="Get the authenticated tenant's branding (logo, banner, theme)",
)
def get_branding(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrandingResponse:
    return SettingsService(db).get_branding(current_user)


@router.put(
    "/branding",
    response_model=BrandingResponse,
    summary="Replace the authenticated tenant's branding",
)
def update_branding(
    payload: BrandingUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrandingResponse:
    return SettingsService(db).update_branding(payload, current_user)


@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Get the authenticated tenant's storefront content (tagline, description, contact)",
)
def get_profile(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    return SettingsService(db).get_profile(current_user)


@router.put(
    "/profile",
    response_model=ProfileResponse,
    summary="Replace the authenticated tenant's storefront content",
)
def update_profile(
    payload: ProfileUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    return SettingsService(db).update_profile(payload, current_user)
