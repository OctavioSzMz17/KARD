"""
Pydantic schemas for the billing module.

NOT IMPLEMENTED — skeleton only. Stripe integration is deferred.
"""

from typing import Literal, Optional

from pydantic import BaseModel


class CheckoutRequest(BaseModel):
    """Payload for POST /api/v1/billing/checkout."""

    plan: Literal["starter", "pro", "enterprise"]


class CheckoutResponse(BaseModel):
    """Returned by POST /api/v1/billing/checkout — contains the Stripe Checkout URL."""

    checkout_url: str
    plan: str


class PlanInfo(BaseModel):
    """Current subscription state for the authenticated tenant."""

    plan: str
    subscription_status: str
    trial_ends_at: Optional[str] = None
