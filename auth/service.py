"""
Authentication business logic (Rules.md section 6).

`AuthService` owns the *rules* — uniqueness checks, password hashing,
lockout policy, token issuance, audit trails — while routes stay thin
(parse request -> call service -> map result/exception to HTTP) and
repositories stay dumb (build queries, no policy decisions).
"""

import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit_logging.audit import log_auth_event, log_operation
from auth.models import LoginRequest, RegisterRequest, TokenResponse
from auth.repository import TenantRepository, UserRepository
from core.exceptions import (
    AccountLockedError,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
)
from core.redis_client import get_redis
from core.security import create_token, hash_password, verify_password
from db.models import Tenant, User
from utils.helpers import generate_id

logger = logging.getLogger("kard.auth")

# Login lockout policy (brute-force protection). Stored in Redis so it's
# ephemeral and shared across workers; if Redis is unreachable we fail
# OPEN (allow the attempt, just log it) rather than locking everyone out.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 minutes


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.tenants = TenantRepository(db)
        self.users = UserRepository(db)

    # ------------------------------------------------------------------
    # Registration — US-003
    # ------------------------------------------------------------------
    def register(self, payload: RegisterRequest) -> TokenResponse:
        """
        Create a new tenant plus its first admin user, atomically.

        The email must be unique *globally* — across both `tenants.email`
        and `users.email` — because a single address identifies a single
        login identity in KARD (Rules.md section 6 / 9).
        """
        email = payload.email.lower()

        if self.tenants.get_by_email(email) is not None:
            raise EmailAlreadyExistsError(email)
        if self.users.get_by_email(email) is not None:
            raise EmailAlreadyExistsError(email)

        tenant = Tenant(
            id=generate_id("t"),
            business_name=payload.business_name,
            business_type=payload.business_type.value,
            email=email,
            phone=payload.phone,
            config={},
            is_active=True,
        )
        user = User(
            id=generate_id("u"),
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(payload.password),
            role="business_admin",
            is_active=True,
        )

        try:
            self.tenants.create(tenant)
            self.users.create(user)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.warning("Registration race detected for email=%s", email)
            raise EmailAlreadyExistsError(email)

        log_operation(
            tenant_id=tenant.id,
            user_id=user.id,
            action="REGISTER_TENANT",
            resource_id=tenant.id,
            after={
                "business_name": tenant.business_name,
                "business_type": tenant.business_type,
                "email": tenant.email,
            },
        )
        log_auth_event(email=email, success=True, reason="registration")

        token = self._issue_token(user, tenant)
        return self._token_response(token, user, tenant)

    # ------------------------------------------------------------------
    # Login — US-001
    # ------------------------------------------------------------------
    def login(self, payload: LoginRequest, ip_address: Optional[str] = None) -> TokenResponse:
        """
        Authenticate by email/password.

        Applies a Redis-backed lockout after `MAX_FAILED_ATTEMPTS`
        consecutive failures within `LOCKOUT_SECONDS`. Every attempt —
        success or failure — is recorded via `log_auth_event` (Rules.md
        section 11.4), and credentials never leak into logs or responses.
        """
        email = payload.email.lower()
        redis_client = get_redis()
        lock_key = f"auth:lockout:{email}"
        attempts_key = f"auth:failed:{email}"

        if redis_client is not None:
            try:
                if redis_client.exists(lock_key):
                    log_auth_event(email=email, success=False, ip_address=ip_address, reason="account_locked")
                    raise AccountLockedError(email)
            except AccountLockedError:
                raise
            except Exception:
                logger.warning("Redis lockout check failed; continuing without it", exc_info=True)
                redis_client = None

        user = self.users.get_by_email(email)

        if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
            self._register_failed_attempt(redis_client, attempts_key, lock_key, email)
            log_auth_event(email=email, success=False, ip_address=ip_address, reason="invalid_credentials")
            raise InvalidCredentialsError(email)

        tenant = self.tenants.get_by_id(user.tenant_id)
        if tenant is None or not tenant.is_active:
            log_auth_event(email=email, success=False, ip_address=ip_address, reason="tenant_inactive")
            raise InvalidCredentialsError(email)

        # Successful login: clear failure counters and stamp last_login.
        if redis_client is not None:
            try:
                redis_client.delete(attempts_key, lock_key)
            except Exception:
                logger.warning("Redis cleanup after successful login failed", exc_info=True)

        self.users.record_login(user)
        self.db.commit()

        log_auth_event(email=email, success=True, ip_address=ip_address, reason="login")
        log_operation(tenant_id=tenant.id, user_id=user.id, action="LOGIN", resource_id=user.id)

        token = self._issue_token(user, tenant)
        return self._token_response(token, user, tenant)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _register_failed_attempt(self, redis_client, attempts_key: str, lock_key: str, email: str) -> None:
        """Increment the failure counter and lock the account once it crosses the threshold."""
        if redis_client is None:
            return
        try:
            attempts = redis_client.incr(attempts_key)
            if attempts == 1:
                redis_client.expire(attempts_key, LOCKOUT_SECONDS)
            if attempts >= MAX_FAILED_ATTEMPTS:
                redis_client.set(lock_key, "1", ex=LOCKOUT_SECONDS)
                logger.warning("Account locked after %s failed attempts: email=%s", attempts, email)
        except Exception:
            logger.warning("Redis failed-attempt bookkeeping failed; continuing", exc_info=True)

    @staticmethod
    def _issue_token(user: User, tenant: Tenant) -> str:
        """
        Build the JWT carrying exactly the claims Rules.md section 1.6
        requires: user_id, tenant_id, business_type, role.
        """
        return create_token(
            {
                "user_id": user.id,
                "tenant_id": tenant.id,
                "business_type": tenant.business_type,
                "role": user.role,
            }
        )

    @staticmethod
    def _token_response(token: str, user: User, tenant: Tenant) -> TokenResponse:
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user_id=user.id,
            tenant_id=tenant.id,
            role=user.role,
            business_type=tenant.business_type,
        )
