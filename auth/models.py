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


class RegisterRequest(BaseModel):
    """
    US-003: SaaS Owner registers a new business; KARD creates the
    tenant record plus its first admin user in one step.
    """

    business_name: str = Field(..., min_length=2, max_length=255)
    business_type: BusinessType
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=20)

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
