"""
Audit logging helpers (Rules.md section 11).

`log_operation` records every CRUD/business operation with
before/after snapshots; `log_auth_event` is the authentication
specialization that always records success/failure, the email
attempted, and the source IP (Rules.md section 11.4).

Both emit through the standard `logging` module so they flow
through whatever handlers `audit_logging.logger.setup_logging`
configures, and can later be persisted to the `audit_logs` table
by a dedicated sink without changing call sites.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger("kard.audit")


def log_operation(
    tenant_id: Optional[str],
    user_id: Optional[str],
    action: str,
    resource_id: Optional[str] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
) -> None:
    """Log a single CRUD/business operation (e.g. CREATE_PRODUCT, REGISTER_TENANT)."""
    logger.info(
        "AUDIT | tenant_id=%s | user_id=%s | action=%s | resource_id=%s",
        tenant_id,
        user_id,
        action,
        resource_id,
        extra={
            "tenant_id": tenant_id,
            "user_id": user_id,
            "action": action,
            "resource_id": resource_id,
            "before": before,
            "after": after,
        },
    )


def log_auth_event(
    email: str,
    success: bool,
    ip_address: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Log an authentication attempt: email, success/failure, IP, reason."""
    logger.info(
        "AUTH | email=%s | success=%s | ip=%s | reason=%s",
        email,
        success,
        ip_address,
        reason,
        extra={
            "email": email,
            "success": success,
            "ip_address": ip_address,
            "reason": reason,
        },
    )
