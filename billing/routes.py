"""
Billing endpoints — Stripe Checkout + Webhook.

NOT IMPLEMENTED — skeleton only. Routes are registered but return 501.
"""

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from billing.models import CheckoutRequest, CheckoutResponse, PlanInfo
from core.dependencies import CurrentUser, get_current_user
from db.database import get_db

router = APIRouter()

_NOT_IMPLEMENTED = JSONResponse(
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    content={"success": False, "error": "Billing not yet implemented"},
)


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_200_OK,
    summary="[NOT IMPLEMENTED] Create a Stripe Checkout Session",
)
def create_checkout(
    payload: CheckoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Creates a Stripe Checkout Session for the authenticated tenant.
    Returns a URL to redirect the user to for payment.

    NOT IMPLEMENTED — returns 501 until Stripe is wired up.
    """
    return _NOT_IMPLEMENTED


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="[NOT IMPLEMENTED] Stripe webhook receiver",
    include_in_schema=False,
)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receives and verifies Stripe webhook events.
    No auth — Stripe authenticates via HMAC signature on the payload.

    NOT IMPLEMENTED — returns 501 until Stripe is wired up.
    """
    return _NOT_IMPLEMENTED


@router.get(
    "/plan",
    response_model=PlanInfo,
    summary="[NOT IMPLEMENTED] Get current subscription plan",
)
def get_plan(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns the tenant's current plan and subscription status."""
    return _NOT_IMPLEMENTED
