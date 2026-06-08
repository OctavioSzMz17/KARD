"""
Shared FastAPI dependencies: authentication and tenant context.

Rules.md section 2 requires every protected route to depend on
`get_current_user`, and every repository query to be scoped via
`Depends(get_tenant_id)` — tenant_id must NEVER be trusted from the
request body/path/query, only from the verified JWT.
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.redis_client import get_redis
from core.security import decode_token
from db.database import get_db
from db.models import User as UserModel

logger = logging.getLogger("kard.dependencies")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


class CurrentUser(BaseModel):
    """
    Authenticated request context, derived from a verified JWT and
    cross-checked against the database.

    Carries exactly the claims Rules.md section 1.6 requires the
    token to hold (`user_id`, `tenant_id`, `business_type`, `role`),
    plus `email` for convenience/audit logging. Downstream code reads
    tenant_id from here — never from request params — to enforce
    multi-tenant isolation (Rules.md section 7).
    """

    user_id: str
    tenant_id: str
    business_type: str
    role: str
    email: str


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """
    Decode and validate the bearer token, then load + verify the user.

    Steps (Rules.md sections 1.2/1.3/1.7):
      1. Decode the JWT (signature + expiration).
      2. Reject tokens on the logout blacklist (best-effort via Redis).
      3. Re-load the user scoped by (tenant_id, user_id) from the DB so
         a deactivated/deleted account loses access immediately, even
         if its token has not expired yet.
    """
    try:
        payload = decode_token(token)
    except JWTError:
        raise _CREDENTIALS_EXCEPTION

    user_id = payload.get("user_id")
    tenant_id = payload.get("tenant_id")
    business_type = payload.get("business_type")
    role = payload.get("role")

    if not all([user_id, tenant_id, business_type, role]):
        raise _CREDENTIALS_EXCEPTION

    redis_client = get_redis()
    if redis_client is not None:
        try:
            if redis_client.exists(f"auth:blacklist:{token}"):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception:
            logger.warning("Redis blacklist check failed; continuing", exc_info=True)

    db_user = (
        db.query(UserModel)
        .filter(
            UserModel.id == user_id,
            UserModel.tenant_id == tenant_id,
            UserModel.is_active.is_(True),
        )
        .first()
    )
    if db_user is None:
        raise _CREDENTIALS_EXCEPTION

    return CurrentUser(
        user_id=db_user.id,
        tenant_id=db_user.tenant_id,
        business_type=business_type,
        role=db_user.role,
        email=db_user.email,
    )


def get_tenant_id(current_user: CurrentUser = Depends(get_current_user)) -> str:
    """Extract tenant_id from the verified token context (never from the request)."""
    return current_user.tenant_id


def get_user_id(current_user: CurrentUser = Depends(get_current_user)) -> str:
    """Extract user_id from the verified token context."""
    return current_user.user_id


# `auto_error=False` makes this scheme return `None` instead of raising 401
# when no `Authorization` header is present — needed for the one-time
# saas_admin bootstrap (US-018), the only case where an *anonymous* caller
# is allowed to hit `POST /auth/users`.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_optional_current_user(
    token: str | None = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[CurrentUser]:
    """
    Resolve the caller if a bearer token was supplied, or `None` if the
    request is anonymous.

    A *missing* token means "anonymous" (returns `None`); a *present but
    invalid/expired* token still raises 401 — only the absence of
    credentials is treated as "no caller", never a bad credential.
    """
    if token is None:
        return None
    return await get_current_user(token=token, db=db)


def require_role(*roles: str):
    """
    Build a dependency that only lets callers with one of `roles` through,
    rejecting everyone else with 403 (Rules.md section 2 — reusable
    auth/authorization dependencies). Use for routes restricted to a
    specific role, e.g. `Depends(require_role("saas_admin"))`.
    """

    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this operation",
            )
        return current_user

    return _check


__all__ = [
    "oauth2_scheme",
    "oauth2_scheme_optional",
    "CurrentUser",
    "get_current_user",
    "get_optional_current_user",
    "get_tenant_id",
    "get_user_id",
    "require_role",
]
