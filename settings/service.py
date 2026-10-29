"""
Business logic for tenant settings (branding + profile).

Both live inside `Tenant.config` (JSONB) under their own key — no migration.

CONCURRENCY (important): `branding` and `profile` share the same JSONB column.
A naive read-modify-write of one can clobber the other when the frontend fires
both PUTs at once (lost update). Every update therefore reads the tenant row
with `get_by_id_for_update()` (SELECT ... FOR UPDATE), which holds a row lock
until commit and serializes concurrent updates.

On update we also invalidate the storefront cache for the tenant's slug so
public visitors see the change without waiting for the TTL.
"""

import logging

from sqlalchemy.orm import Session

from auth.repository import TenantRepository
from core.dependencies import CurrentUser
from core.exceptions import TenantNotFoundError
from core.redis_client import invalidate_storefront_cache
from core.tenant_config import merge_config_key, read_branding, read_fulfillment, read_profile
from settings.models import (
    BrandingResponse,
    BrandingUpdate,
    ContactModel,
    FulfillmentResponse,
    FulfillmentUpdate,
    ProfileResponse,
    ProfileUpdate,
    ThemeModel,
)

logger = logging.getLogger("kard.settings")


class SettingsService:
    def __init__(self, db: Session):
        self.db = db
        self.tenants = TenantRepository(db)

    # ------------------------------------------------------------------
    # Branding (logo / banner / theme)
    # ------------------------------------------------------------------

    def get_branding(self, current_user: CurrentUser) -> BrandingResponse:
        tenant = self.tenants.get_by_id(current_user.tenant_id)
        if tenant is None:
            raise TenantNotFoundError(current_user.tenant_id)
        return self._branding_response(read_branding(tenant.config))

    def update_branding(self, payload: BrandingUpdate, current_user: CurrentUser) -> BrandingResponse:
        tenant = self._locked_tenant(current_user.tenant_id)

        theme = None
        if payload.theme is not None:
            theme = {"primary": payload.theme.primary, "accent": payload.theme.accent}

        branding = {
            "logo_url": payload.logo_url,
            "banner_url": payload.banner_url,
            "theme": theme,
        }
        self._commit_config(tenant, "branding", branding)
        logger.info("Branding updated for tenant_id=%s", tenant.id)
        return self._branding_response(read_branding(tenant.config))

    # ------------------------------------------------------------------
    # Profile (tagline / description / contact)
    # ------------------------------------------------------------------

    def get_profile(self, current_user: CurrentUser) -> ProfileResponse:
        tenant = self.tenants.get_by_id(current_user.tenant_id)
        if tenant is None:
            raise TenantNotFoundError(current_user.tenant_id)
        return self._profile_response(read_profile(tenant.config))

    def update_profile(self, payload: ProfileUpdate, current_user: CurrentUser) -> ProfileResponse:
        tenant = self._locked_tenant(current_user.tenant_id)

        contact = payload.contact or ContactModel()
        profile = {
            "tagline": payload.tagline,
            "description": payload.description,
            "contact": {
                "address": contact.address,
                "phone": contact.phone,
                "email": contact.email,
                "whatsapp": contact.whatsapp,
                "maps_url": contact.maps_url,
                "hours": [{"days": h.days, "hours": h.hours} for h in contact.hours],
                "social": {
                    "facebook": contact.social.facebook,
                    "instagram": contact.social.instagram,
                    "tiktok": contact.social.tiktok,
                    "x": contact.social.x,
                    "youtube": contact.social.youtube,
                },
            },
        }
        self._commit_config(tenant, "profile", profile)
        logger.info("Profile updated for tenant_id=%s", tenant.id)
        return self._profile_response(read_profile(tenant.config))

    # ------------------------------------------------------------------
    # Entrega (modos ofrecidos + tarifa de envío)
    # ------------------------------------------------------------------

    def get_fulfillment(self, current_user: CurrentUser) -> FulfillmentResponse:
        tenant = self.tenants.get_by_id(current_user.tenant_id)
        if tenant is None:
            raise TenantNotFoundError(current_user.tenant_id)
        return FulfillmentResponse(**read_fulfillment(tenant.config))

    def update_fulfillment(self, payload: FulfillmentUpdate, current_user: CurrentUser) -> FulfillmentResponse:
        tenant = self._locked_tenant(current_user.tenant_id)

        fulfillment = {
            "dine_in": payload.dine_in,
            "takeaway": payload.takeaway,
            "pickup": payload.pickup,
            "delivery": payload.delivery,
            # Los importes se guardan como string: el JSONB no distingue
            # decimales de flotantes y redondearía centavos.
            "shipping_flat_fee": str(payload.shipping_flat_fee),
            "free_shipping_above": (
                str(payload.free_shipping_above) if payload.free_shipping_above is not None else None
            ),
            "delivery_notes": payload.delivery_notes,
        }
        self._commit_config(tenant, "fulfillment", fulfillment)
        logger.info("Fulfillment updated for tenant_id=%s", tenant.id)
        return FulfillmentResponse(**read_fulfillment(tenant.config))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _locked_tenant(self, tenant_id: str):
        """Row-locked tenant read — prevents lost updates on the shared config JSONB."""
        tenant = self.tenants.get_by_id_for_update(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(tenant_id)
        return tenant

    def _commit_config(self, tenant, key: str, value: dict) -> None:
        """Replace one config key (preserving the rest), commit, refresh cache."""
        tenant.config = merge_config_key(tenant.config, key, value)
        self.tenants.save(tenant)
        self.db.commit()
        if tenant.slug:
            invalidate_storefront_cache(tenant.slug)

    @staticmethod
    def _branding_response(branding: dict) -> BrandingResponse:
        theme_data = branding.get("theme") or {}
        return BrandingResponse(
            logo_url=branding.get("logo_url"),
            banner_url=branding.get("banner_url"),
            theme=ThemeModel(primary=theme_data.get("primary"), accent=theme_data.get("accent")),
        )

    @staticmethod
    def _profile_response(profile: dict) -> ProfileResponse:
        return ProfileResponse(
            tagline=profile.get("tagline"),
            description=profile.get("description"),
            contact=ContactModel(**profile["contact"]),
        )
