"""
Data-access layer for the saasadmin module (Rules.md section 3 —
Repository Pattern). Pure query building — no policy decisions; those
live in `SaasAdminService`.
"""

from typing import Optional

from sqlalchemy.orm import Session

from db.models import Tenant


class TenantRepository:
    """Read/write access to the `tenants` table, scoped to platform-owner operations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> Optional[Tenant]:
        return self.db.query(Tenant).filter(Tenant.email == email).first()

    def create(self, tenant: Tenant) -> Tenant:
        self.db.add(tenant)
        self.db.flush()  # assign defaults / surface unique violations early
        return tenant
