"""
Pydantic schemas for the consumers module (global B2C identities).

The consumer JWT carries { consumer_id, type: "consumer" } — no tenant_id.
Tenant context is always resolved from the storefront slug in the URL.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class ConsumerRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: Optional[str] = Field(default=None, max_length=100)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(ch.isdigit() for ch in v):
            raise ValueError("La contraseña debe llevar al menos un número")
        if not any(ch.isalpha() for ch in v):
            raise ValueError("La contraseña debe llevar al menos una letra")
        return v


class ConsumerLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class ConsumerResponse(BaseModel):
    consumer_id: str
    email: str
    name: Optional[str] = None
    is_active: bool
    created_at: datetime


class ConsumerTokenResponse(BaseModel):
    """
    Returned by register and login.

    The token carries { consumer_id, type: "consumer" } — no tenant_id,
    no role. The `type` claim is what distinguishes it from a B2B token.
    """

    access_token: str
    token_type: str = "bearer"
    consumer_id: str
