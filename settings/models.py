"""
Pydantic schemas for tenant settings:
  - branding: logo, banner, theme (lo visual)
  - profile:  tagline, description, contact (contenido editorial)

Contract is fixed by the frontend — do NOT rename fields:
  branding: logo_url, banner_url, theme.primary, theme.accent
  profile:  tagline, description,
            contact.{address,phone,email,whatsapp,maps_url,hours,social}
            social.{facebook,instagram,tiktok,x,youtube}
"""

import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")


def _require_http_url(v: Optional[str]) -> Optional[str]:
    """Shared validator: value must be an http(s) URL, or None."""
    if v is None:
        return v
    if not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError("La URL debe iniciar con http:// o https://")
    return v


class ThemeModel(BaseModel):
    primary: Optional[str] = None
    accent: Optional[str] = None

    @field_validator("primary", "accent")
    @classmethod
    def _valid_hex(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _HEX_RE.match(v):
            raise ValueError("El color debe ser hexadecimal en formato #RRGGBB")
        return v


class BrandingResponse(BaseModel):
    logo_url: Optional[str] = None
    banner_url: Optional[str] = None
    theme: Optional[ThemeModel] = None


class BrandingUpdate(BaseModel):
    """
    Full replacement of the branding object. Fields omitted default to null,
    so a partial payload clears the missing fields (simplest to reason about).
    """

    logo_url: Optional[str] = Field(default=None, max_length=2048)
    banner_url: Optional[str] = Field(default=None, max_length=2048)
    theme: Optional[ThemeModel] = None

    @field_validator("logo_url", "banner_url")
    @classmethod
    def _valid_url(cls, v: Optional[str]) -> Optional[str]:
        return _require_http_url(v)


# ---------------------------------------------------------------------------
# Profile — contenido editorial de la tienda
# ---------------------------------------------------------------------------


class HourEntry(BaseModel):
    """Horario en texto libre: { days: "Lunes a Viernes", hours: "9:00 – 18:00" }."""

    days: str = Field(..., max_length=60)
    hours: str = Field(..., max_length=60)


class SocialModel(BaseModel):
    facebook: Optional[str] = Field(default=None, max_length=2048)
    instagram: Optional[str] = Field(default=None, max_length=2048)
    tiktok: Optional[str] = Field(default=None, max_length=2048)
    x: Optional[str] = Field(default=None, max_length=2048)
    youtube: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("facebook", "instagram", "tiktok", "x", "youtube")
    @classmethod
    def _valid_url(cls, v: Optional[str]) -> Optional[str]:
        return _require_http_url(v)


class ContactModel(BaseModel):
    address: Optional[str] = Field(default=None, max_length=300)
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[EmailStr] = None
    whatsapp: Optional[str] = Field(default=None, max_length=40)
    maps_url: Optional[str] = Field(default=None, max_length=2048)
    hours: list[HourEntry] = Field(default_factory=list, max_length=10)
    social: SocialModel = Field(default_factory=SocialModel)

    @field_validator("maps_url")
    @classmethod
    def _valid_url(cls, v: Optional[str]) -> Optional[str]:
        return _require_http_url(v)


class ProfileResponse(BaseModel):
    tagline: Optional[str] = None
    description: Optional[str] = None
    contact: ContactModel = Field(default_factory=ContactModel)


class ProfileUpdate(BaseModel):
    """
    Full replacement of the profile object (same semantics as BrandingUpdate):
    omitted fields are stored as null / [].
    """

    tagline: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    contact: Optional[ContactModel] = None
