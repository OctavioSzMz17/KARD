"""
Authentication business logic (Rules.md section 6).

`AuthService` owns the *rules* — uniqueness checks, password hashing,
lockout policy, token issuance, audit trails — while routes stay thin
(parse request -> call service -> map result/exception to HTTP) and
repositories stay dumb (build queries, no policy decisions).
"""

import logging
import re
import unicodedata
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit_logging.audit import log_auth_event, log_operation
from auth.models import (
    CreateUserRequest,
    LoginRequest,
    TenantRegistrationRequest,
    TenantRegistrationResponse,
    TokenResponse,
    UserResponse,
    UserRole,
)
from auth.repository import TenantRepository, UserRepository
from core.dependencies import CurrentUser
from core.exceptions import (
    AccountLockedError,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    TenantAlreadyExistsError,
    TenantNotFoundError,
    UserCreationNotAllowedError,
)
from core.redis_client import get_redis
from core.security import create_token, hash_password, verify_password
from db.models import Tenant, User
from notifications.service import NotificationService
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
    # Self-service tenant registration — B2B2C onboarding
    # ------------------------------------------------------------------
    def register_tenant(self, payload: TenantRegistrationRequest) -> TenantRegistrationResponse:
        """
        Create a tenant + its first business_admin in one atomic commit.

        Returns a JWT so the new admin lands directly in the dashboard
        without a separate login step. The slug is derived automatically
        from `business_name` and guaranteed to be unique.
        """
        email = payload.email.lower()

        # Dual uniqueness check: same email cannot be used twice anywhere
        if self.users.get_by_email(email) is not None:
            raise EmailAlreadyExistsError(email)
        if self.tenants.get_by_email(email) is not None:
            raise TenantAlreadyExistsError(email)

        slug = self._generate_unique_slug(payload.business_name)

        tenant = Tenant(
            id=generate_id("t"),
            business_name=payload.business_name.strip(),
            business_type=payload.business_type.value,
            email=email,
            slug=slug,
            is_public=False,
            is_active=True,
        )

        user = User(
            id=generate_id("u"),
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(payload.password),
            role=UserRole.business_admin.value,
            is_active=True,
        )

        try:
            self.tenants.create(tenant)
            self.users.create(user)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.warning("register_tenant race detected for email=%s", email)
            raise EmailAlreadyExistsError(email)

        log_operation(
            tenant_id=tenant.id,
            user_id=user.id,
            action="REGISTER_TENANT",
            resource_id=tenant.id,
            after={
                "business_name": tenant.business_name,
                "business_type": tenant.business_type,
                "slug": tenant.slug,
                "email": tenant.email,
            },
        )
        logger.info("New tenant registered: tenant_id=%s slug=%s", tenant.id, tenant.slug)

        NotificationService.tenant_welcome(
            email=tenant.email,
            business_name=tenant.business_name,
            slug=tenant.slug,
        )

        token = create_token(
            {
                "user_id": user.id,
                "tenant_id": tenant.id,
                "business_type": tenant.business_type,
                "role": user.role,
            }
        )
        return TenantRegistrationResponse(
            access_token=token,
            token_type="bearer",
            user_id=user.id,
            tenant_id=tenant.id,
            role=user.role,
            business_type=tenant.business_type,
            slug=tenant.slug,
        )

    def _generate_unique_slug(self, business_name: str) -> str:
        """Derive a URL-safe slug from business_name, appending -2/-3/... if taken."""
        base = self._slugify(business_name)
        if not self.tenants.get_by_slug(base):
            return base
        for suffix in range(2, 10_000):
            candidate = f"{base}-{suffix}"
            if not self.tenants.get_by_slug(candidate):
                return candidate
        # Practically unreachable
        raise RuntimeError(f"Could not generate a unique slug from '{business_name}'")

    @staticmethod
    def _slugify(text: str) -> str:
        """'Papelería Don Jorge' → 'papeleria-don-jorge' (max 55 chars, room for -9999 suffix)."""
        # Decompose accented chars (á → a + combining accent), then drop non-ASCII
        text = unicodedata.normalize("NFKD", text)
        text = text.encode("ascii", "ignore").decode("ascii")
        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        text = text.strip("-")
        return text[:55]

    # ------------------------------------------------------------------
    # User creation — US-018
    # ------------------------------------------------------------------
    def create_user(self, payload: CreateUserRequest, current_user: Optional[CurrentUser]) -> UserResponse:
        """
        Create a user and assign it to a tenant + role.

        One mechanism, three legitimate shapes (Rules.md section 1.10):
          - **Bootstrap**: anonymous caller creates the platform's one
            and only `saas_admin`, but ONLY while none exists yet.
          - **saas_admin caller**: may create `business_admin`/`staff`
            for any active tenant they specify (e.g. onboarding a new
            business right after `POST /saasadmin/tenants`).
          - **business_admin caller**: may create `business_admin`/
            `staff` for THEIR OWN tenant only — `tenant_id` always comes
            from the verified JWT, never the request body (section 7).

        Returns the created user's public profile — never a token; the
        new account authenticates for itself via `/auth/login`.
        """
        email = payload.email.lower()
        if self.users.get_by_email(email) is not None:
            raise EmailAlreadyExistsError(email)

        if payload.role == UserRole.saas_admin:
            target_tenant_id = self._resolve_bootstrap(payload, current_user)
        else:
            target_tenant_id = self._resolve_target_tenant(payload, current_user)

        user = User(
            id=generate_id("u"),
            tenant_id=target_tenant_id,
            email=email,
            password_hash=hash_password(payload.password),
            role=payload.role.value,
            is_active=True,
        )

        try:
            self.users.create(user)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.warning("User-creation race detected for email=%s", email)
            raise EmailAlreadyExistsError(email)

        log_operation(
            tenant_id=target_tenant_id,
            user_id=current_user.user_id if current_user else user.id,
            action="CREATE_USER",
            resource_id=user.id,
            after={"email": user.email, "role": user.role, "tenant_id": user.tenant_id},
        )

        tenant = self.tenants.get_by_id(target_tenant_id)
        return UserResponse(
            user_id=user.id,
            email=user.email,
            role=user.role,
            tenant_id=user.tenant_id,
            business_type=tenant.business_type,
            is_active=user.is_active,
        )

    def _resolve_bootstrap(self, payload: CreateUserRequest, current_user: Optional[CurrentUser]) -> str:
        """
        Validate the one-time, anonymous-only path that mints the
        platform's single `saas_admin`. Closes permanently the moment
        one exists — afterwards even an existing saas_admin cannot mint
        another through this endpoint (by design: there is only one).
        """
        if current_user is not None:
            raise UserCreationNotAllowedError("saas_admin accounts can only be created via the one-time bootstrap")
        if self.users.count_by_role(UserRole.saas_admin.value) > 0:
            raise UserCreationNotAllowedError("Bootstrap is closed: a platform owner already exists")
        if not payload.tenant_id:
            raise UserCreationNotAllowedError("tenant_id is required to create the platform owner account")

        tenant = self.tenants.get_by_id(payload.tenant_id)
        if tenant is None or not tenant.is_active:
            raise TenantNotFoundError(payload.tenant_id)
        return tenant.id

    def _resolve_target_tenant(self, payload: CreateUserRequest, current_user: Optional[CurrentUser]) -> str:
        """Resolve which tenant a new business_admin/staff account belongs to, by the caller's role."""
        if current_user is None:
            raise UserCreationNotAllowedError("Authentication is required to create business_admin/staff accounts")

        if current_user.role == UserRole.business_admin.value:
            return current_user.tenant_id  # never trust the request body (Rules.md section 7)

        if current_user.role == UserRole.saas_admin.value:
            if not payload.tenant_id:
                raise UserCreationNotAllowedError("tenant_id is required when creating a user as saas_admin")
            tenant = self.tenants.get_by_id(payload.tenant_id)
            if tenant is None or not tenant.is_active:
                raise TenantNotFoundError(payload.tenant_id)
            return tenant.id

        raise UserCreationNotAllowedError("Only saas_admin or business_admin accounts can create users")

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
