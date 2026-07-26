"""
Helpers to read/write the tenant's editable content inside `Tenant.config`
(JSONB) — no migration needed.

Two independent namespaces live there:

    config = {
      "branding": { logo_url, banner_url, theme{primary,accent} },   # visual
      "profile":  { tagline, description, contact{...} }             # editorial
    }

The `read_*` helpers always return a fully-normalized dict (every key
present, values possibly None) so callers build responses without
None-checks — this is what guarantees the frontend contract:
`contact`, `social` and `theme` are never absent, `hours` is always a list.

NOTE on concurrency: `branding` and `profile` share the same JSONB column,
so a read-modify-write of one can clobber the other if two PUTs race.
Callers MUST read the tenant row with a row lock (see
`TenantRepository.get_by_id_for_update`) before calling `merge_config_key`.
"""

from typing import Any, Optional


def read_branding(config: Optional[dict]) -> dict:
    """Normalized branding: logo_url, banner_url, theme{primary,accent}."""
    branding = (config or {}).get("branding") or {}
    theme = branding.get("theme") or {}
    return {
        "logo_url": branding.get("logo_url"),
        "banner_url": branding.get("banner_url"),
        "theme": {
            "primary": theme.get("primary"),
            "accent": theme.get("accent"),
        },
    }


def read_profile(config: Optional[dict]) -> dict:
    """Normalized profile: tagline, description, contact{...} (always present)."""
    profile = (config or {}).get("profile") or {}
    contact = profile.get("contact") or {}
    social = contact.get("social") or {}
    raw_hours = contact.get("hours") or []

    return {
        "tagline": profile.get("tagline"),
        "description": profile.get("description"),
        "contact": {
            "address": contact.get("address"),
            "phone": contact.get("phone"),
            "email": contact.get("email"),
            "whatsapp": contact.get("whatsapp"),
            "maps_url": contact.get("maps_url"),
            "hours": [
                {"days": h.get("days"), "hours": h.get("hours")}
                for h in raw_hours
                if isinstance(h, dict)
            ],
            "social": {
                "facebook": social.get("facebook"),
                "instagram": social.get("instagram"),
                "tiktok": social.get("tiktok"),
                "x": social.get("x"),
                "youtube": social.get("youtube"),
            },
        },
    }


def merge_config_key(config: Optional[dict], key: str, value: dict) -> dict:
    """
    Return a NEW config dict with `key` replaced, preserving every other key.

    Returning a new object (instead of mutating in place) is what makes
    SQLAlchemy flag the JSONB column as changed.
    """
    new_config: dict[str, Any] = dict(config or {})
    new_config[key] = value
    return new_config
