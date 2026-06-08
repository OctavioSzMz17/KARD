"""
Business logic for the products module (Rules.md section 4 / 6).

`ProductService` is the only place that:
  - validates JSONB metadata against the tenant's business_type schema,
  - enforces stock never goes negative,
  - keeps `products.inventory_count` and `inventory_transactions` in
    sync inside a single atomic commit, and
  - writes the audit trail (Rules.md section 11).

Routes stay thin (Rules.md section 5) and never touch the repository
or `Session` directly.
"""

import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit_logging.audit import log_operation
from core.dependencies import CurrentUser
from core.exceptions import (
    DuplicateProductNameError,
    InsufficientStockError,
    ProductNotFoundError,
)
from db.models import InventoryTransaction, Product
from products.models import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
    StockChangeRequest,
    StockChangeResponse,
    validate_metadata,
)
from products.repository import InventoryTransactionRepository, ProductRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.products")


class ProductService:
    def __init__(self, db: Session):
        self.db = db
        self.products = ProductRepository(db)
        self.inventory = InventoryTransactionRepository(db)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_product(self, payload: ProductCreate, current_user: CurrentUser) -> ProductResponse:
        """Create a catalog item for the caller's tenant (US — Product CRUD)."""
        validate_metadata(current_user.business_type, payload.metadata)

        if self.products.get_by_name(current_user.tenant_id, payload.name) is not None:
            raise DuplicateProductNameError(payload.name)

        product = Product(
            id=generate_id("p"),
            tenant_id=current_user.tenant_id,
            product_type=payload.product_type.value,
            name=payload.name,
            description=payload.description,
            price=payload.price,
            inventory_count=payload.inventory_count,
            low_stock_threshold=payload.low_stock_threshold,
            metadata_=payload.metadata,
            created_by=current_user.user_id,
        )

        try:
            self.products.create(product)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.warning(
                "Product-creation race detected for tenant_id=%s name=%s", current_user.tenant_id, payload.name
            )
            raise DuplicateProductNameError(payload.name)

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="CREATE_PRODUCT",
            resource_id=product.id,
            after=self._snapshot(product),
        )
        return self._to_response(product)

    def list_products(
        self,
        current_user: CurrentUser,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        product_type: Optional[str] = None,
    ) -> ProductListResponse:
        """Paginated, optionally name-searched/type-filtered catalog listing."""
        items, total = self.products.list_products(
            tenant_id=current_user.tenant_id,
            page=page,
            page_size=page_size,
            search=search,
            product_type=product_type,
        )
        return ProductListResponse(
            items=[self._to_response(p) for p in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_product(self, product_id: str, current_user: CurrentUser) -> ProductResponse:
        product = self._require_product(product_id, current_user.tenant_id)
        return self._to_response(product)

    def update_product(
        self, product_id: str, payload: ProductUpdate, current_user: CurrentUser
    ) -> ProductResponse:
        """Partial update (PATCH semantics) — only fields explicitly provided change."""
        product = self._require_product(product_id, current_user.tenant_id)
        before = self._snapshot(product)

        if payload.name is not None and payload.name != product.name:
            existing = self.products.get_by_name(current_user.tenant_id, payload.name)
            if existing is not None and existing.id != product.id:
                raise DuplicateProductNameError(payload.name)
            product.name = payload.name

        if payload.description is not None:
            product.description = payload.description
        if payload.price is not None:
            product.price = payload.price
        if payload.low_stock_threshold is not None:
            product.low_stock_threshold = payload.low_stock_threshold
        if payload.metadata is not None:
            # Metadata is replaced wholesale (not merged) — partial JSONB
            # would silently drop required keys past schema validation.
            validate_metadata(current_user.business_type, payload.metadata)
            product.metadata_ = payload.metadata

        try:
            self.products.save(product)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.warning(
                "Product-update race detected for tenant_id=%s product_id=%s", current_user.tenant_id, product_id
            )
            raise DuplicateProductNameError(payload.name or product.name)

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="UPDATE_PRODUCT",
            resource_id=product.id,
            before=before,
            after=self._snapshot(product),
        )
        return self._to_response(product)

    def soft_delete_product(self, product_id: str, current_user: CurrentUser) -> None:
        """Flip `is_deleted` — hard deletes are never performed (Rules.md / schema)."""
        product = self._require_product(product_id, current_user.tenant_id)
        before = self._snapshot(product)

        product.is_deleted = True
        self.products.save(product)
        self.db.commit()

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="DELETE_PRODUCT",
            resource_id=product.id,
            before=before,
        )

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    def sell_product(
        self, product_id: str, payload: StockChangeRequest, current_user: CurrentUser
    ) -> StockChangeResponse:
        """Decrease stock — never below zero (Rules.md / `products_inventory_non_negative`)."""
        product = self._require_product(product_id, current_user.tenant_id)
        if payload.quantity > product.inventory_count:
            raise InsufficientStockError(
                f"Cannot sell {payload.quantity} units — only {product.inventory_count} in stock"
            )
        return self._apply_stock_change(
            product=product,
            action="sell",
            quantity_change=-payload.quantity,
            current_user=current_user,
            notes=payload.notes,
            audit_action="SELL_PRODUCT",
        )

    def restock_product(
        self, product_id: str, payload: StockChangeRequest, current_user: CurrentUser
    ) -> StockChangeResponse:
        """Increase stock (e.g. a new shipment arrived)."""
        product = self._require_product(product_id, current_user.tenant_id)
        return self._apply_stock_change(
            product=product,
            action="restock",
            quantity_change=payload.quantity,
            current_user=current_user,
            notes=payload.notes,
            audit_action="RESTOCK_PRODUCT",
        )

    def get_low_stock(self, current_user: CurrentUser) -> list[ProductResponse]:
        """Products whose `inventory_count` has fallen below their own `low_stock_threshold`."""
        items = self.products.get_low_stock(current_user.tenant_id)
        return [self._to_response(p) for p in items]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_stock_change(
        self,
        product: Product,
        action: str,
        quantity_change: int,
        current_user: CurrentUser,
        notes: Optional[str],
        audit_action: str,
    ) -> StockChangeResponse:
        """
        Atomically: mutate `inventory_count`, append an immutable
        `inventory_transactions` row, commit both together, then write
        the audit-log entry (Rules.md section 11 — "sell + inventory_
        transaction + audit log" as one unit).
        """
        quantity_before = product.inventory_count
        quantity_after = quantity_before + quantity_change
        product.inventory_count = quantity_after

        transaction = InventoryTransaction(
            product_id=product.id,
            tenant_id=current_user.tenant_id,
            action=action,
            quantity_change=quantity_change,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            user_id=current_user.user_id,
            notes=notes,
        )

        try:
            self.products.save(product)
            self.inventory.create(transaction)
            self.db.commit()
        except IntegrityError:
            # A concurrent request could slip past the in-Python check
            # above (TOCTOU); the DB's CHECK constraints are the final
            # backstop — translate that race into the same domain error.
            self.db.rollback()
            logger.warning(
                "Inventory race detected for tenant_id=%s product_id=%s action=%s",
                current_user.tenant_id,
                product.id,
                action,
            )
            raise InsufficientStockError(f"Stock changed concurrently — retry the {action} operation")

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action=audit_action,
            resource_id=product.id,
            before={"inventory_count": quantity_before},
            after={"inventory_count": quantity_after, "quantity_change": quantity_change, "notes": notes},
        )

        return StockChangeResponse(
            product_id=product.id,
            action=action,
            quantity_change=quantity_change,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            inventory_count=product.inventory_count,
        )

    def _require_product(self, product_id: str, tenant_id: str) -> Product:
        product = self.products.get_by_id(tenant_id, product_id)
        if product is None:
            raise ProductNotFoundError(product_id)
        return product

    @staticmethod
    def _snapshot(product: Product) -> dict:
        """Plain-dict snapshot for audit before/after payloads — never the ORM object itself."""
        return {
            "name": product.name,
            "description": product.description,
            "price": str(product.price),
            "low_stock_threshold": product.low_stock_threshold,
            "metadata": product.metadata_,
            "is_deleted": product.is_deleted,
        }

    @staticmethod
    def _to_response(product: Product) -> ProductResponse:
        return ProductResponse(
            product_id=product.id,
            tenant_id=product.tenant_id,
            product_type=product.product_type,
            name=product.name,
            description=product.description,
            price=product.price,
            inventory_count=product.inventory_count,
            low_stock_threshold=product.low_stock_threshold,
            metadata=product.metadata_,
            is_deleted=product.is_deleted,
        )
