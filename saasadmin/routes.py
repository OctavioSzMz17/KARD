"""
SaaS-admin endpoints (Rules.md section 9 — routes stay thin):

  POST /api/v1/saasadmin/tenants — register a new business / tenant (US-003)

Restricted to `saas_admin` via `Depends(require_role("saas_admin"))` —
only the platform owner manages tenants at this level.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core.dependencies import CurrentUser, require_role
from db.database import get_db
from saasadmin.models import TenantCreateRequest, TenantResponse
from saasadmin.service import SaasAdminService

router = APIRouter()


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new business/tenant (saas_admin only)",
)
def create_tenant(
    payload: TenantCreateRequest,
    current_user: CurrentUser = Depends(require_role("saas_admin")),
    db: Session = Depends(get_db),
) -> TenantResponse:
    return SaasAdminService(db).create_tenant(payload, current_user)
