"""
SaaS-admin business logic (Rules.md section 6).

Everything here is reachable only by the platform owner (`saas_admin`,
enforced by `Depends(require_role("saas_admin"))` in routes.py) — these
are operations on the platform itself, not on any one tenant's data.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit_logging.audit import log_operation
from core.dependencies import CurrentUser
from core.exceptions import EmailAlreadyExistsError
from db.models import Tenant
from saasadmin.models import TenantCreateRequest, TenantResponse
from saasadmin.repository import TenantRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.saasadmin")


class SaasAdminService:
    def __init__(self, db: Session):
        self.db = db
        self.tenants = TenantRepository(db)

    def create_tenant(self, payload: TenantCreateRequest, current_user: CurrentUser) -> TenantResponse:
        """
        Register a new business (US-003): creates ONLY the `tenants`
        record. Onboarding its first admin is a deliberate separate
        step (US-018, `POST /auth/users`) — a tenant can exist before
        it has anyone able to log into it.
        """
        email = payload.email.lower()
        if self.tenants.get_by_email(email) is not None:
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

        try:
            self.tenants.create(tenant)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.warning("Tenant-creation race detected for email=%s", email)
            raise EmailAlreadyExistsError(email)

        log_operation(
            tenant_id=tenant.id,
            user_id=current_user.user_id,
            action="CREATE_TENANT",
            resource_id=tenant.id,
            after={
                "business_name": tenant.business_name,
                "business_type": tenant.business_type,
                "email": tenant.email,
            },
        )

        return TenantResponse(
            tenant_id=tenant.id,
            business_name=tenant.business_name,
            business_type=tenant.business_type,
            email=tenant.email,
            phone=tenant.phone,
            is_active=tenant.is_active,
        )
