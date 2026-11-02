"""
Authentication & identity endpoints (Rules.md section 9 — routes stay thin):

  POST /api/v1/auth/users — create a user, assigned to a tenant + role (US-018)
  POST /api/v1/auth/login — authenticate by email/password (US-001)

Routes only: parse/validate the request (via Pydantic models), resolve
the DB session and (optional) caller identity, delegate to `AuthService`,
and return its result. All business/authorization logic — uniqueness
checks, role rules, hashing, lockouts, token shape — lives in the
service layer.
"""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session
from typing import Optional

from auth.models import CreateUserRequest, LoginRequest, MeResponse, TenantRegistrationRequest, TenantRegistrationResponse, TokenResponse, UserResponse
from auth.service import AuthService
from core.dependencies import CurrentUser, get_current_user, get_optional_current_user
from db.database import get_db

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    """Best-effort source IP for audit logging (Rules.md section 11.4)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user and assign it to a tenant + role (US-018)",
)
def create_user(
    payload: CreateUserRequest,
    current_user: Optional[CurrentUser] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    """
    Authentication is *optional* at the route level on purpose: the
    one-time saas_admin bootstrap must work for an anonymous caller,
    while every other combination (business_admin/staff, by a
    saas_admin or business_admin) requires one. `AuthService.create_user`
    enforces exactly which combinations are legal.
    """
    return AuthService(db).create_user(payload, current_user)


@router.post(
    "/register-tenant",
    response_model=TenantRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Self-service tenant registration — creates tenant + first business_admin in one step",
)
def register_tenant(payload: TenantRegistrationRequest, db: Session = Depends(get_db)) -> TenantRegistrationResponse:
    """
    Public endpoint — no auth required.

    Creates the tenant record, generates a unique slug, creates the first
    business_admin user, and returns a ready-to-use JWT so the new admin
    lands directly in the dashboard.
    """
    return AuthService(db).register_tenant(payload)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with email and password",
)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    return AuthService(db).login(payload, ip_address=_client_ip(request))


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Who am I and what can I do — read from the database on every call",
)
def me(current_user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    """
    Cualquier cuenta autenticada puede preguntar por sí misma; no pide permiso
    porque solo devuelve lo suyo. La pantalla lo usa al arrancar para no fiarse
    de lo que guardó al iniciar sesión: si cambiaron su puesto, lo sabe ya.
    """
    return AuthService.me(current_user)
