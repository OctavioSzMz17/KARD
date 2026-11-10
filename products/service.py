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
    DuplicateProductCodeError,
    DuplicateProductNameError,
    InsufficientStockError,
    ProductNotFoundError,
)
from core.redis_client import invalidate_storefront_cache
from db.models import InventoryTransaction, Product, Tenant
from images.repository import ImageRepository
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
from products.stock_policy import tracks_stock
from utils.helpers import generate_id

logger = logging.getLogger("kard.products")


class ProductService:
    def __init__(self, db: Session):
        self.db = db
        self.products = ProductRepository(db)
        self.inventory = InventoryTransactionRepository(db)
        self.image_repo = ImageRepository(db)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_product(self, payload: ProductCreate, current_user: CurrentUser) -> ProductResponse:
        """Create a catalog item for the caller's tenant (US — Product CRUD)."""
        validate_metadata(current_user.business_type, payload.product_type.value, payload.metadata)

        if self.products.get_by_name(current_user.tenant_id, payload.name) is not None:
            raise DuplicateProductNameError(payload.name)
        self._assert_code_free(current_user.tenant_id, "sku", payload.sku)
        self._assert_code_free(current_user.tenant_id, "barcode", payload.barcode)

        product = Product(
            id=generate_id("p"),
            tenant_id=current_user.tenant_id,
            product_type=payload.product_type.value,
            name=payload.name,
            description=payload.description,
            price=payload.price,
            inventory_count=payload.inventory_count,
            low_stock_threshold=payload.low_stock_threshold,
            sku=payload.sku,
            barcode=payload.barcode,
            metadata_=payload.metadata,
            created_by=current_user.user_id,
        )

        try:
            self.products.create(product)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            logger.warning(
                "Product-creation race detected for tenant_id=%s name=%s", current_user.tenant_id, payload.name
            )
            raise self._integrity_error(exc, payload.name, payload.sku, payload.barcode)

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="CREATE_PRODUCT",
            resource_id=product.id,
            after=self._snapshot(product),
        )
        self._bust_storefront_cache(current_user.tenant_id)
        return self._to_response(product, business_type=current_user.business_type)

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
            items=self._to_responses(items, current_user.tenant_id, current_user.business_type),
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_product(self, product_id: str, current_user: CurrentUser) -> ProductResponse:
        product = self._require_product(product_id, current_user.tenant_id)
        return self._to_response(product, business_type=current_user.business_type)

    def find_by_code(self, code: str, current_user: CurrentUser) -> ProductResponse:
        """
        Producto que corresponde a un código escaneado.

        Orden: código de barras exacto, SKU (sin distinguir mayúsculas) y, por
        último, el id del producto. El id cubre las etiquetas de productos que
        no tienen ni código de barras ni SKU: se imprimen con su id.
        """
        code = code.strip()
        tenant_id = current_user.tenant_id
        product = (
            self.products.get_by_code(tenant_id, "barcode", code)
            or self.products.get_by_code(tenant_id, "sku", code)
            or self.products.get_by_id(tenant_id, code)
        )
        if product is None:
            raise ProductNotFoundError(code)
        return self._to_response(product, business_type=current_user.business_type)

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

        # None = no tocar; cadena vacía = quitar el código.
        for field in ("sku", "barcode"):
            nuevo = getattr(payload, field)
            if nuevo is None:
                continue
            nuevo = nuevo or None
            if nuevo != getattr(product, field):
                if nuevo:
                    self._assert_code_free(current_user.tenant_id, field, nuevo, exclude_id=product.id)
                setattr(product, field, nuevo)

        if payload.description is not None:
            product.description = payload.description
        if payload.price is not None:
            product.price = payload.price
        if payload.low_stock_threshold is not None:
            product.low_stock_threshold = payload.low_stock_threshold
        if payload.metadata is not None:
            # Metadata is replaced wholesale (not merged) — partial JSONB
            # would silently drop required keys past schema validation.
            validate_metadata(current_user.business_type, product.product_type, payload.metadata)
            product.metadata_ = payload.metadata

        try:
            self.products.save(product)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            logger.warning(
                "Product-update race detected for tenant_id=%s product_id=%s", current_user.tenant_id, product_id
            )
            raise self._integrity_error(exc, payload.name or product.name, product.sku, product.barcode)

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="UPDATE_PRODUCT",
            resource_id=product.id,
            before=before,
            after=self._snapshot(product),
        )
        self._bust_storefront_cache(current_user.tenant_id)
        return self._to_response(product, business_type=current_user.business_type)

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
        self._bust_storefront_cache(current_user.tenant_id)

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------

    def sell_product(
        self, product_id: str, payload: StockChangeRequest, current_user: CurrentUser
    ) -> StockChangeResponse:
        """Decrease stock — never below zero (Rules.md / `products_inventory_non_negative`)."""
        product = self._require_product(product_id, current_user.tenant_id)
        self._assert_tracks_stock(product, current_user)
        if payload.quantity > product.inventory_count:
            raise InsufficientStockError(
                f"No se pueden vender {payload.quantity} unidades: solo hay {product.inventory_count} en existencias"
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
        self._assert_tracks_stock(product, current_user)
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
        items = [
            p for p in self.products.get_low_stock(current_user.tenant_id)
            if tracks_stock(current_user.business_type, p.product_type)
        ]
        return self._to_responses(items, current_user.tenant_id, current_user.business_type)

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
            raise InsufficientStockError("El inventario cambió mientras se guardaba; intenta de nuevo")

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

    def _bust_storefront_cache(self, tenant_id: str) -> None:
        """Invalidate the Redis catalog cache for this tenant's public storefront, if it has one."""
        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant and tenant.slug:
            invalidate_storefront_cache(tenant.slug)

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
            "sku": product.sku,
            "barcode": product.barcode,
            "metadata": product.metadata_,
            "is_deleted": product.is_deleted,
        }

    def _assert_code_free(
        self, tenant_id: str, field: str, value: str | None, exclude_id: str | None = None
    ) -> None:
        """El SKU o el código de barras no puede repetirse entre los productos vigentes del negocio."""
        if not value:
            return
        existing = self.products.get_by_code(tenant_id, field, value)
        if existing is not None and existing.id != exclude_id:
            raise DuplicateProductCodeError(field, value)

    @staticmethod
    def _integrity_error(exc: IntegrityError, name: str, sku: str | None, barcode: str | None) -> Exception:
        """
        Traduce una colisión que se coló entre la comprobación y el guardado
        (dos personas guardando a la vez) al error que le corresponde: los
        índices únicos de la base son la última línea de defensa.
        """
        detalle = str(getattr(exc, "orig", exc))
        if "uq_products_tenant_sku" in detalle:
            return DuplicateProductCodeError("sku", sku or "")
        if "uq_products_tenant_barcode" in detalle:
            return DuplicateProductCodeError("barcode", barcode or "")
        return DuplicateProductNameError(name)

    @staticmethod
    def _assert_tracks_stock(product: Product, current_user: CurrentUser) -> None:
        """Vender o resurtir un platillo no tiene sentido: se controla por sus insumos."""
        if not tracks_stock(current_user.business_type, product.product_type):
            raise InsufficientStockError(
                "Los platillos no llevan existencias: se controlan por los insumos de su receta"
            )

    def _to_responses(
        self, products: list[Product], tenant_id: str, business_type: Optional[str] = None
    ) -> list[ProductResponse]:
        """
        Varios productos con sus imágenes en UNA consulta.

        Pedirlas producto por producto eran N consultas a la base por cada
        listado (20 productos = 20 viajes a Neon, más de un segundo solo en
        imágenes).
        """
        images = self.image_repo.list_for_owners(tenant_id, "product", [p.id for p in products])
        return [self._to_response(p, images.get(p.id, []), business_type) for p in products]

    def _to_response(
        self, product: Product, raw_images=None, business_type: Optional[str] = None
    ) -> ProductResponse:
        from images.models import ImageResponse
        if raw_images is None:   # un solo producto (detalle, crear, editar): una consulta basta
            raw_images = self.image_repo.list_for_owner(product.tenant_id, "product", product.id)
        images = [
            ImageResponse(
                image_id=img.id,
                owner_type=img.owner_type,
                owner_id=img.owner_id,
                url=img.url,
                alt_text=img.alt_text,
                sort_order=img.sort_order,
                created_at=img.created_at,
            )
            for img in raw_images
        ]
        return ProductResponse(
            product_id=product.id,
            tenant_id=product.tenant_id,
            product_type=product.product_type,
            name=product.name,
            description=product.description,
            price=product.price,
            inventory_count=product.inventory_count,
            low_stock_threshold=product.low_stock_threshold,
            tracks_stock=tracks_stock(business_type, product.product_type),
            metadata=product.metadata_,
            is_deleted=product.is_deleted,
            sku=product.sku,
            barcode=product.barcode,
            images=images,
        )
