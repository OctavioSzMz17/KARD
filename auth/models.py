"""
Pydantic request/response schemas for the auth module
(Rules.md sections 6 & 9 — validation at the entry point).
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class BusinessType(str, Enum):
    """Mirrors `business_type_enum` defined in kard_database.sql."""

    restaurant = "restaurant"
    optician = "optician"


class UserRole(str, Enum):
    """Mirrors `user_role_enum` defined in kard_database.sql."""

    saas_admin = "saas_admin"
    business_admin = "business_admin"
    staff = "staff"


class CreateUserRequest(BaseModel):
    """
    US-018: create a user and explicitly assign it to a tenant + role.

    One shared shape covers every case — onboarding a brand-new
    business's first admin, adding staff later, and the one-time
    saas_admin bootstrap — because the *caller's* identity (or absence
    of one, for bootstrap) is what determines which combinations are
    actually allowed; see `AuthService.create_user`.

    `tenant_id` is optional here on purpose: a `business_admin` caller
    has theirs supplied by the verified JWT (Rules.md section 7 — never
    trusted from the request body), so the field is only meaningful
    when a `saas_admin` targets another tenant or bootstraps itself.
    """

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole
    tenant_id: Optional[str] = Field(default=None, max_length=50)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(ch.isdigit() for ch in v):
            raise ValueError("Password must contain at least one digit")
        if not any(ch.isalpha() for ch in v):
            raise ValueError("Password must contain at least one letter")
        return v


class UserResponse(BaseModel):
    """
    Returned by `POST /auth/users` (US-018).

    Deliberately carries no `access_token` — creating an account for
    someone else must never hand the creator a way to act as them; the
    new user authenticates on their own via `/auth/login`.
    """

    user_id: str
    email: EmailStr
    role: str
    tenant_id: str
    business_type: str
    is_active: bool


class LoginRequest(BaseModel):
    """US-001: Admin login by email/password; tenant is detected server-side."""

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """
    Returned by both `/register` and `/login` (US-001, US-002, US-003).

    Never includes the password or password_hash (Rules.md section 6.10).
    `access_token` carries user_id, tenant_id, business_type and role
    so the frontend/backend always know which tenant's data to load.
    """

    access_token: str
    token_type: str = "bearer"
    user_id: str
    tenant_id: str
    role: str
    business_type: str


class TenantRegistrationRequest(BaseModel):
    """
    Payload for `POST /auth/register-tenant` — self-service B2B onboarding.

    Creates both the tenant record and its first business_admin user in one
    atomic operation. Returns a JWT so the admin lands directly in the panel.
    """

    business_name: str = Field(..., min_length=2, max_length=255)
    business_type: BusinessType
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("business_name")
    @classmethod
    def business_name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("business_name cannot be blank")
        return v

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(ch.isdigit() for ch in v):
            raise ValueError("Password must contain at least one digit")
        if not any(ch.isalpha() for ch in v):
            raise ValueError("Password must contain at least one letter")
        return v


class TenantRegistrationResponse(BaseModel):
    """
    Returned by `POST /auth/register-tenant`.

    Includes a ready-to-use JWT plus the assigned slug so the frontend
    can immediately display the storefront URL to the new admin.
    """

    access_token: str
    token_type: str = "bearer"
    user_id: str
    tenant_id: str
    role: str
    business_type: str
    slug: str
