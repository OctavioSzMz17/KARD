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

from core.permissions import permissions_of, position_of
from core.redis_client import get_redis
from core.security import decode_token
from db.database import get_db
from db.models import User as UserModel

logger = logging.getLogger("kard.dependencies")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="No se pudo validar tu sesión",
    headers={"WWW-Authenticate": "Bearer"},
)


def session_revoked(payload: dict, password_changed_at) -> bool:
    """
    ¿La sesión se abrió antes de que se restableciera la contraseña por correo?

    Si alguien tenía robada una sesión, restablecer la contraseña se la quita. Se compara en segundos
    enteros (el `iat` del token no tiene fracciones): una sesión abierta en el mismo segundo del
    cambio sobrevive, que es justo la que abre quien acaba de poner su contraseña nueva.
    Un token sin `iat` y una cuenta que nunca restableció no se tocan.
    """
    if password_changed_at is None:
        return False
    issued_at = payload.get("iat")
    if issued_at is None:
        return True        # sin fecha de emisión no se puede probar que es posterior al cambio
    return int(issued_at) < int(password_changed_at.timestamp())


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
    # Puesto dentro del negocio (gerente, subgerente…). None = empleado o dueño.
    job_title: Optional[str] = None
    full_name: Optional[str] = None
    employee_code: Optional[str] = None

    @property
    def position(self) -> str:
        """Puesto efectivo (dueño, gerente, subgerente, encargado de área, empleado)."""
        return position_of(self.role, self.job_title)

    @property
    def permissions(self) -> frozenset[str]:
        return permissions_of(self.position)

    def can(self, permission: str) -> bool:
        return permission in self.permissions


# Estas dependencias son `def`, NO `async def`, a propósito: por dentro consultan
# Redis y la base de datos de forma síncrona, y una `async def` corre directo en
# el bucle de eventos, así que mientras esperaba a la base (cientos de ms en
# Neon) congelaba a TODAS las demás peticiones, incluidas las que están en
# caché y deberían tardar 10 ms. Como `def`, FastAPI las ejecuta en un hilo y
# el bucle sigue atendiendo al resto.
def get_current_user(
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
                    detail="Tu sesión ya no es válida; inicia sesión de nuevo",
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
    if db_user is None or session_revoked(payload, db_user.password_changed_at):
        raise _CREDENTIALS_EXCEPTION

    return CurrentUser(
        user_id=db_user.id,
        tenant_id=db_user.tenant_id,
        business_type=business_type,
        role=db_user.role,
        email=db_user.email,
        job_title=db_user.job_title,
        full_name=db_user.full_name,
        employee_code=db_user.employee_code,
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


def get_optional_current_user(
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
    return get_current_user(token=token, db=db)


class CurrentConsumer(BaseModel):
    """
    Authenticated B2C consumer context, derived from a consumer JWT.

    Carries only `consumer_id` and `email` — no tenant_id, no role.
    Tenant context is always resolved from the storefront slug in the URL.
    """

    consumer_id: str
    email: str


def get_current_consumer(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> CurrentConsumer:
    """
    Validate a consumer bearer token and return the authenticated consumer.

    Rejects tokens that are:
      - Missing or malformed
      - Not of type "consumer" (i.e. a B2B token cannot access consumer routes)
      - Pointing to an inactive or deleted consumer
    """
    try:
        payload = decode_token(token)
    except JWTError:
        raise _CREDENTIALS_EXCEPTION

    consumer_id = payload.get("consumer_id")
    token_type = payload.get("type")

    if not consumer_id or token_type != "consumer":
        raise _CREDENTIALS_EXCEPTION

    from db.models import Consumer as ConsumerModel
    db_consumer = (
        db.query(ConsumerModel)
        .filter(ConsumerModel.id == consumer_id, ConsumerModel.is_active.is_(True))
        .first()
    )
    if db_consumer is None or session_revoked(payload, db_consumer.password_changed_at):
        raise _CREDENTIALS_EXCEPTION

    return CurrentConsumer(consumer_id=db_consumer.id, email=db_consumer.email)


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
                detail="No tienes permiso para esta operación",
            )
        return current_user

    _check.roles = roles  # type: ignore[attr-defined]
    return _check


def require_permission(*permissions: str):
    """
    Dependencia que deja pasar solo a quien tenga ALGUNO de `permissions`.

    Es la única forma de proteger una ruta del negocio: el puesto y los
    permisos se leen de la base en cada petición (`get_current_user` recarga al
    usuario), así que un cambio de puesto o una baja surten efecto al instante,
    sin esperar a que caduque el token.

    La función devuelta lleva `.permissions` para que las pruebas puedan
    comprobar que ninguna ruta del negocio quedó sin guardia.
    """

    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not any(current_user.can(p) for p in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para esta operación",
            )
        return current_user

    _check.permissions = permissions  # type: ignore[attr-defined]
    return _check


__all__ = [
    "require_permission",
    "oauth2_scheme",
    "oauth2_scheme_optional",
    "CurrentUser",
    "CurrentConsumer",
    "get_current_user",
    "get_current_consumer",
    "get_optional_current_user",
    "get_tenant_id",
    "get_user_id",
    "require_role",
]
