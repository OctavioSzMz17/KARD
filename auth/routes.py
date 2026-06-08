"""
Authentication endpoints (Rules.md section 9 — routes stay thin):

  POST /api/v1/auth/register — create a new tenant + its admin user
  POST /api/v1/auth/login    — authenticate by email/password

Routes only: parse/validate the request (via Pydantic models), resolve
the DB session, delegate to `AuthService`, and return its result. All
business logic — uniqueness checks, hashing, lockouts, token shape —
lives in the service layer.
"""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from auth.models import LoginRequest, RegisterRequest, TokenResponse
from auth.service import AuthService
from db.database import get_db

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    """Best-effort source IP for audit logging (Rules.md section 11.4)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new business (tenant) and its admin user",
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    return AuthService(db).register(payload)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email and password",
)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    return AuthService(db).login(payload, ip_address=_client_ip(request))
