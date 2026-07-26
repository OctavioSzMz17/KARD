"""
Security utilities: password hashing (bcrypt, min 12 rounds) and
JWT token creation / decoding.

All secrets are read from `settings` (see core/config.py) — never
hardcoded (Rules.md sections 1 and 13).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from core.config import settings

# bcrypt with a minimum of 12 rounds, as required by Rules.md section 1.4
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt (min 12 rounds)."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Check a plaintext password against a stored bcrypt hash.

    Treats a malformed/corrupt stored hash as "does not match" rather
    than letting passlib's `ValueError` bubble up — a bad hash must
    never turn a login attempt into a 500 (it should look identical to
    a wrong password to the caller, see Rules.md section 1 on not
    leaking account-existence details).
    """
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def create_token(data: dict[str, Any]) -> str:
    """
    Create a signed JWT from the given claims.

    Adds `iat` (issued-at) and `exp` (expiration) claims based on
    `settings.JWT_EXPIRATION_HOURS`. Expected claims for KARD tokens:
    user_id, tenant_id, business_type, role (see Rules.md section 1.6).
    """
    now = datetime.now(timezone.utc)
    to_encode = data.copy()
    to_encode.update(
        {
            "iat": now,
            "exp": now + timedelta(hours=settings.JWT_EXPIRATION_HOURS),
        }
    )
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT.

    Raises `jose.JWTError` (or a subclass, e.g. `ExpiredSignatureError`)
    if the token is invalid, malformed, or expired. Callers (e.g.
    `get_current_user` in feature/auth) are responsible for translating
    that into an HTTP 401 response.
    """
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
