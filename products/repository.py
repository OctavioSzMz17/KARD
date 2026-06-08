"""
Data-access layer for the products module (Rules.md section 3 —
Repository Pattern).

Pure query building — no business rules, no validation, no commits.
EVERY query here filters by `tenant_id` (Rules.md section 7 — multi-
tenant isolation): a row that belongs to another tenant must be as
invisible as one that doesn't exist at all.
"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import InventoryTransaction, Product


class ProductRepository:
    """Read/write access to the `products` table, always tenant-scoped."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, tenant_id: str, product_id: str) -> Optional[Product]:
        """
        Fetch a single, non-deleted product owned by `tenant_id`.

        Cross-tenant lookups intentionally return `None` (-> 404 to the
        caller) rather than distinguishing "doesn't exist" from "exists
        but isn't yours" — Rules.md section 7.5.
        """
        return (
            self.db.query(Product)
            .filter(Product.tenant_id == tenant_id, Product.id == product_id, Product.is_deleted.is_(False))
            .first()
        )

    def get_by_name(self, tenant_id: str, name: str) -> Optional[Product]:
        """
        Look up by exact name within a tenant, *including* soft-deleted
        rows — `uq_product_name_tenant` is a plain unique constraint with
        no partial-index exception for `is_deleted`, so a soft-deleted
        product still occupies its name at the database level. Checking
        here lets the service raise a clean `DuplicateProductNameError`
        instead of surfacing a raw `IntegrityError`.
        """
        return self.db.query(Product).filter(Product.tenant_id == tenant_id, Product.name == name).first()

    def list_products(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        product_type: Optional[str] = None,
    ) -> tuple[list[Product], int]:
        """Paginated, optionally filtered/searched listing. Returns `(items, total_count)`."""
        query = self.db.query(Product).filter(Product.tenant_id == tenant_id, Product.is_deleted.is_(False))

        if search:
            query = query.filter(Product.name.ilike(f"%{search}%"))
        if product_type:
            query = query.filter(Product.product_type == product_type)

        total = query.count()
        items = (
            query.order_by(Product.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def get_low_stock(self, tenant_id: str) -> list[Product]:
        """Active products whose stock has fallen below their own alert threshold."""
        return (
            self.db.query(Product)
            .filter(
                Product.tenant_id == tenant_id,
                Product.is_deleted.is_(False),
                Product.inventory_count < Product.low_stock_threshold,
            )
            .order_by(Product.inventory_count.asc())
            .all()
        )

    def create(self, product: Product) -> Product:
        self.db.add(product)
        self.db.flush()  # assign defaults / surface FK & unique violations early
        return product

    def save(self, product: Product) -> Product:
        """Persist in-place mutations made to an already-tracked `Product` instance."""
        self.db.add(product)
        self.db.flush()
        return product


class InventoryTransactionRepository:
    """
    Append-only access to the `inventory_transactions` table.

    No update/delete methods exist on purpose — this table is an
    immutable movement log (Rules.md section 11); every row is created
    once, alongside the `Product.inventory_count` change it records,
    inside the same DB transaction (see `ProductService._apply_stock_change`).
    """

    def __init__(self, db: Session):
        self.db = db

    def create(self, transaction: InventoryTransaction) -> InventoryTransaction:
        self.db.add(transaction)
        self.db.flush()
        return transaction

    def count_for_product(self, tenant_id: str, product_id: str) -> int:
        """Used in tests/diagnostics to confirm a stock change left a trail."""
        return (
            self.db.query(func.count(InventoryTransaction.id))
            .filter(InventoryTransaction.tenant_id == tenant_id, InventoryTransaction.product_id == product_id)
            .scalar()
            or 0
        )
