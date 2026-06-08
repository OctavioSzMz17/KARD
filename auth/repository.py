"""
Data-access layer for the auth module (Rules.md section 3 — Repository Pattern).

Repositories are the *only* place allowed to build SQLAlchemy queries for
their tables. Services call into them and never touch `Session`/models
directly — that keeps tenant-scoping rules and query shape centralized
and testable in one place.
"""

from typing import Optional

from sqlalchemy.orm import Session

from db.models import Tenant, User


class TenantRepository:
    """Read/write access to the `tenants` table."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, tenant_id: str) -> Optional[Tenant]:
        return self.db.query(Tenant).filter(Tenant.id == tenant_id).first()

    def get_by_email(self, email: str) -> Optional[Tenant]:
        """Tenants carry their own contact email, distinct from user emails."""
        return self.db.query(Tenant).filter(Tenant.email == email).first()

    def create(self, tenant: Tenant) -> Tenant:
        self.db.add(tenant)
        self.db.flush()  # assign defaults / surface FK & unique violations early
        return tenant


class UserRepository:
    """
    Read/write access to the `users` table.

    NOTE: user emails are globally unique (Rules.md / kard_database.sql
    `users.email UNIQUE`), so lookups for authentication purposes are
    intentionally *not* tenant-scoped — the tenant is derived from the
    matched user, not the other way around. Once a `tenant_id` is known
    (e.g. from a verified JWT), all *other* user queries must filter by
    it (Rules.md section 7).
    """

    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> Optional[User]:
        return self.db.query(User).filter(User.email == email).first()

    def get_by_id(self, user_id: str, tenant_id: str) -> Optional[User]:
        return (
            self.db.query(User)
            .filter(User.id == user_id, User.tenant_id == tenant_id)
            .first()
        )

    def create(self, user: User) -> User:
        self.db.add(user)
        self.db.flush()
        return user

    def record_login(self, user: User) -> User:
        """Stamp `last_login` on a successful authentication."""
        from datetime import datetime, timezone

        user.last_login = datetime.now(timezone.utc)
        self.db.add(user)
        self.db.flush()
        return user
