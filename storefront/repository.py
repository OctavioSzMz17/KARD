"""
Data-access layer for the public storefront.

`StorefrontRepository` resolves slug → tenant and delegates product
queries to the existing `ProductRepository` so the same query logic,
indexes, and tenant-scoping rules are reused without duplication.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from db.database import SessionLocal
from db.models import ConsumerEvent, Tenant
from products.repository import ProductRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.storefront")


def record_event_detached(
    tenant_id: str,
    event_type: str,
    payload: Optional[dict] = None,
    consumer_id: Optional[str] = None,
) -> None:
    """
    Record an analytics event on its own session, after the response left.

    Meant for FastAPI `BackgroundTasks`: the request's session is closed by
    then, and writing the event inline made every public page wait for an
    extra round trip (insert + commit) to the database. A failure here only
    costs one analytics row, so it is logged and swallowed.
    """
    db = SessionLocal()
    try:
        StorefrontRepository(db).record_event(tenant_id, event_type, payload, consumer_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Could not record %s event for tenant_id=%s", event_type, tenant_id, exc_info=True)
    finally:
        db.close()


class StorefrontRepository:
    def __init__(self, db: Session):
        self.db = db
        self.products = ProductRepository(db)

    def get_public_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """
        Resolve slug → tenant.

        Returns None (→ 404) for:
          - Unknown slug
          - Tenant exists but is_public=False (not yet activated)
          - Tenant exists but is_active=False (suspended)

        The 404 is intentionally indistinguishable from "doesn't exist"
        — same philosophy as cross-tenant product lookups (Rules.md §7.5).
        """
        return (
            self.db.query(Tenant)
            .filter(
                Tenant.slug == slug,
                Tenant.is_public.is_(True),
                Tenant.is_active.is_(True),
            )
            .first()
        )

    def record_event(
        self,
        tenant_id: str,
        event_type: str,
        payload: Optional[dict] = None,
        consumer_id: Optional[str] = None,
    ) -> None:
        """Append a row to consumer_events. Fire-and-forget — never blocks the response."""
        event = ConsumerEvent(
            id=generate_id("ce", 20),
            consumer_id=consumer_id,
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
        )
        self.db.add(event)
        # No flush/commit here — the service owns the transaction boundary.
