"""
Order business logic — converts a Redis cart into a DB-persisted order,
descontando el stock del producto final Y de sus insumos (receta).

Flujo de checkout:
  1. Analiza cada producto del carrito bajo bloqueo de fila (SELECT FOR UPDATE):
     valida su propio stock y el de cada insumo de su receta.
  2. Si algo no alcanza:
       - on_insufficient_stock="reject" (default): rollback y OrderStockConflictError
         → 409 con el detalle estructurado para que el front pregunte al usuario.
       - on_insufficient_stock="skip": omite esos productos y sigue con el resto,
         reportándolos en `skipped`. Si no queda nada, 409 (all_skipped).
  3. Descuenta stock del final y de cada insumo, registra inventory_transactions,
     crea Order + OrderItems, COMMIT, y borra el carrito de Redis.
"""

import json
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from core.exceptions import (
    CartEmptyError,
    OrderNotFoundError,
    OrderStockConflictError,
    StorefrontNotFoundError,
)
from core.redis_client import get_redis
from db.models import Consumer, InventoryTransaction, Order, OrderItem, Product, ProductRecipe, Tenant
from orders.models import (
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    SaleResponse,
    SalesReportResponse,
    SalesSummary,
    SkippedItemResponse,
)
from orders.repository import OrderRepository
from storefront.repository import StorefrontRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.orders")


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = OrderRepository(db)

    def create_from_cart(
        self,
        consumer_id: str,
        slug: str,
        on_insufficient_stock: str = "reject",
    ) -> OrderResponse:
        tenant = StorefrontRepository(self.db).get_public_tenant_by_slug(slug)
        if tenant is None:
            raise StorefrontNotFoundError(slug)

        r = get_redis()
        cart_key = f"cart:{consumer_id}:{slug}"
        raw = r.get(cart_key) if r else None
        cart: dict = json.loads(raw) if raw else {}

        if not cart:
            raise CartEmptyError("El carrito está vacío")

        fulfillable, unavailable = self._analyze_cart(tenant.id, cart)

        if unavailable:
            if on_insufficient_stock != "skip":
                self.db.rollback()  # release row locks, change nothing
                raise OrderStockConflictError(unavailable)
            if not fulfillable:
                self.db.rollback()
                raise OrderStockConflictError(unavailable, all_skipped=True)

        skipped = [
            SkippedItemResponse(
                product_id=u["product_id"],
                product_name=u["product_name"],
                reason=u["reason"],
            )
            for u in unavailable
        ]

        order, items_data = self._apply(tenant, consumer_id, fulfillable)

        self.db.commit()
        logger.info(
            "Order created: order_id=%s consumer=%s tenant=%s total=%s skipped=%d",
            order.id, consumer_id, tenant.id, order.total, len(skipped),
        )

        # Clear cart only after successful commit
        if r:
            r.delete(cart_key)

        return self._to_response(order, items_data, slug, tenant.business_name, skipped)

    # ------------------------------------------------------------------
    # Phase 1 — analysis (row-locks every final + insumo involved)
    # ------------------------------------------------------------------
    def _analyze_cart(self, tenant_id: str, cart: dict) -> tuple[list[dict], list[dict]]:
        fulfillable: list[dict] = []
        unavailable: list[dict] = []

        for product_id, entry in cart.items():
            quantity = int(entry["quantity"])
            display_name = entry.get("name") or product_id

            product = (
                self.db.query(Product)
                .filter(
                    Product.id == product_id,
                    Product.tenant_id == tenant_id,
                    Product.is_deleted.is_(False),
                )
                .with_for_update()
                .first()
            )

            if product is None:
                unavailable.append({
                    "product_id": product_id,
                    "product_name": display_name,
                    "requested": quantity,
                    "reason": "unavailable",
                })
                continue

            recipes = (
                self.db.query(ProductRecipe)
                .filter(
                    ProductRecipe.final_product_id == product.id,
                    ProductRecipe.tenant_id == tenant_id,
                )
                .all()
            )

            product_short = product.inventory_count < quantity
            missing: list[dict] = []
            ingredient_plan: list[tuple] = []

            for rec in recipes:
                insumo = (
                    self.db.query(Product)
                    .filter(
                        Product.id == rec.insumo_product_id,
                        Product.tenant_id == tenant_id,
                        Product.is_deleted.is_(False),
                    )
                    .with_for_update()
                    .first()
                )
                need = rec.quantity * quantity
                unit = None
                if insumo is not None and isinstance(insumo.metadata_, dict):
                    unit = insumo.metadata_.get("unidad_medida")

                if insumo is None:
                    missing.append({
                        "insumo_id": rec.insumo_product_id,
                        "insumo_name": "(insumo no disponible)",
                        "required": need,
                        "available": 0,
                        "unit": unit,
                    })
                    continue

                ingredient_plan.append((insumo, need))
                if insumo.inventory_count < need:
                    missing.append({
                        "insumo_id": insumo.id,
                        "insumo_name": insumo.name,
                        "required": need,
                        "available": insumo.inventory_count,
                        "unit": unit,
                    })

            if product_short or missing:
                if product_short and missing:
                    reason = "product_and_ingredient"
                elif product_short:
                    reason = "product_shortage"
                else:
                    reason = "ingredient_shortage"

                item = {
                    "product_id": product.id,
                    "product_name": product.name,
                    "requested": quantity,
                    "reason": reason,
                }
                if product_short:
                    item["available"] = product.inventory_count
                if missing:
                    item["missing_ingredients"] = missing
                unavailable.append(item)
                continue

            fulfillable.append({
                "product": product,
                "quantity": quantity,
                "ingredients": ingredient_plan,
            })

        return fulfillable, unavailable

    # ------------------------------------------------------------------
    # Phase 2 — apply (decrement stock + write order). No commit here.
    # ------------------------------------------------------------------
    def _apply(self, tenant, consumer_id: str, fulfillable: list[dict]) -> tuple[Order, list[dict]]:
        total = Decimal("0.00")
        items_data: list[dict] = []

        for plan in fulfillable:
            product = plan["product"]
            quantity = plan["quantity"]

            before = product.inventory_count
            product.inventory_count = before - quantity
            self.db.add(InventoryTransaction(
                product_id=product.id,
                tenant_id=tenant.id,
                action="sell",
                quantity_change=-quantity,
                quantity_before=before,
                quantity_after=product.inventory_count,
                notes="Venta B2C (producto final)",
            ))

            # Cascade: decrement each ingredient of the recipe
            for insumo, need in plan["ingredients"]:
                ins_before = insumo.inventory_count
                insumo.inventory_count = ins_before - need
                self.db.add(InventoryTransaction(
                    product_id=insumo.id,
                    tenant_id=tenant.id,
                    action="sell",
                    quantity_change=-need,
                    quantity_before=ins_before,
                    quantity_after=insumo.inventory_count,
                    notes=f"Consumo por venta de '{product.name}'",
                ))

            subtotal = product.price * quantity
            total += subtotal
            items_data.append({
                "product_id": product.id,
                "product_name": product.name,
                "quantity": quantity,
                "unit_price": product.price,
                "subtotal": subtotal,
            })

        order = Order(
            id=generate_id("ord", 20),
            consumer_id=consumer_id,
            tenant_id=tenant.id,
            status="pending",
            total=total,
        )
        self.db.add(order)
        self.db.flush()

        for item in items_data:
            self.db.add(OrderItem(
                id=generate_id("oi", 20),
                order_id=order.id,
                product_id=item["product_id"],
                product_name=item["product_name"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                subtotal=item["subtotal"],
            ))

        return order, items_data

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_order(self, consumer_id: str, order_id: str) -> OrderResponse:
        order = self.repo.get_by_id(order_id, consumer_id)
        if order is None:
            raise OrderNotFoundError(order_id)
        return self._order_to_response(order)

    def list_sales(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        date_from=None,
        date_to=None,
        status: str | None = None,
        search: str | None = None,
    ) -> SalesReportResponse:
        """
        Ventas del negocio para el reporte del dashboard.

        El resumen se calcula sobre todo el conjunto filtrado (no sobre la
        página), porque el reporte descargado debe cuadrar aunque el usuario
        esté viendo la página 1 de 8.
        """
        rows, total = self.repo.list_by_tenant(
            tenant_id, page, page_size, date_from, date_to, status, search
        )
        summary = self.repo.summarize_by_tenant(tenant_id, date_from, date_to, status, search)

        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()

        return SalesReportResponse(
            business_name=tenant.business_name if tenant else "",
            items=[self._sale_to_response(order, consumer) for order, consumer in rows],
            summary=SalesSummary(**summary),
            total=total,
            page=page,
            page_size=page_size,
        )

    def update_sale_status(self, tenant_id: str, order_id: str, new_status: str) -> SaleResponse:
        """Cambia el estado de una venta del negocio autenticado."""
        order = self.repo.get_by_id_for_tenant(order_id, tenant_id)
        if order is None:
            raise OrderNotFoundError("Order not found")

        order.status = new_status
        self.db.commit()
        self.db.refresh(order)

        consumer = self.db.query(Consumer).filter(Consumer.id == order.consumer_id).first()
        return self._sale_to_response(order, consumer)

    @staticmethod
    def _sale_to_response(order: Order, consumer) -> SaleResponse:
        items = [
            OrderItemResponse(
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                subtotal=item.subtotal,
            )
            for item in order.items
        ]
        return SaleResponse(
            order_id=order.id,
            status=order.status,
            total=order.total,
            item_count=sum(i.quantity for i in items),
            # El consumer puede no tener nombre cargado; el correo siempre está.
            customer_name=(consumer.name or "").strip() or consumer.email,
            customer_email=consumer.email,
            items=items,
            created_at=order.created_at,
        )

    def list_orders(self, consumer_id: str, page: int, page_size: int) -> OrderListResponse:
        orders, total = self.repo.list_by_consumer(consumer_id, page, page_size)
        return OrderListResponse(
            items=[self._order_to_response(o) for o in orders],
            total=total,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _to_response(order, items_data, slug, business_name, skipped=None) -> OrderResponse:
        return OrderResponse(
            order_id=order.id,
            slug=slug,
            business_name=business_name,
            status=order.status,
            total=order.total,
            items=[
                OrderItemResponse(
                    product_id=i["product_id"],
                    product_name=i["product_name"],
                    quantity=i["quantity"],
                    unit_price=i["unit_price"],
                    subtotal=i["subtotal"],
                )
                for i in items_data
            ],
            skipped=skipped or [],
            created_at=order.created_at,
        )

    @staticmethod
    def _order_to_response(order: Order) -> OrderResponse:
        return OrderResponse(
            order_id=order.id,
            slug=order.tenant.slug or "",
            business_name=order.tenant.business_name,
            status=order.status,
            total=order.total,
            items=[
                OrderItemResponse(
                    product_id=item.product_id,
                    product_name=item.product_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    subtotal=item.subtotal,
                )
                for item in order.items
            ],
            skipped=[],
            created_at=order.created_at,
        )
