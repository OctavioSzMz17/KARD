"""
Pydantic request/response schemas for the saasadmin module
(Rules.md sections 6 & 9 — validation at the entry point).
"""

from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from auth.models import BusinessType


class TenantCreateRequest(BaseModel):
    """
    US-003: SaaS Owner registers a new business (tenant only — its
    people are onboarded separately afterward via US-018).
    """

    business_name: str = Field(..., min_length=2, max_length=255)
    business_type: BusinessType
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=20)

    @field_validator("business_name")
    @classmethod
    def business_name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("business_name cannot be blank")
        return v


class TenantResponse(BaseModel):
    """Returned by `POST /saasadmin/tenants`."""

    tenant_id: str
    business_name: str
    business_type: str
    email: EmailStr
    phone: Optional[str] = None
    is_active: bool
