"""
Data-access layer for the public storefront.

`StorefrontRepository` resolves slug → tenant and delegates product
queries to the existing `ProductRepository` so the same query logic,
indexes, and tenant-scoping rules are reused without duplication.
"""

from typing import Optional

from sqlalchemy.orm import Session

from db.models import ConsumerEvent, Tenant
from products.repository import ProductRepository
from utils.helpers import generate_id


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
