"""
Consumer identity endpoints — public registration/login + authenticated profile.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from consumers.models import ConsumerLogin, ConsumerRegister, ConsumerResponse, ConsumerTokenResponse
from consumers.service import ConsumerService
from core.dependencies import CurrentConsumer, get_current_consumer
from db.database import get_db

router = APIRouter()


@router.post(
    "/register",
    response_model=ConsumerTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a global consumer account",
)
def register(payload: ConsumerRegister, db: Session = Depends(get_db)) -> ConsumerTokenResponse:
    """Public — no auth required. Returns a ready-to-use consumer JWT."""
    return ConsumerService(db).register(payload)


@router.post(
    "/login",
    response_model=ConsumerTokenResponse,
    summary="Authenticate as a consumer",
)
def login(payload: ConsumerLogin, db: Session = Depends(get_db)) -> ConsumerTokenResponse:
    """Public — no auth required."""
    return ConsumerService(db).login(payload)


@router.get(
    "/me",
    response_model=ConsumerResponse,
    summary="Get authenticated consumer profile",
)
def get_me(
    current_consumer: CurrentConsumer = Depends(get_current_consumer),
    db: Session = Depends(get_db),
) -> ConsumerResponse:
    """Requires a valid consumer JWT (Bearer token with type=consumer)."""
    return ConsumerService(db).get_me(current_consumer.consumer_id)
