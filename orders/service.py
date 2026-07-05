"""
Order business logic — converts a Redis cart into a DB-persisted order.

The critical section is atomic:
  SELECT FOR UPDATE on every product → check stock → decrement → write
  inventory_transaction → create order + items → COMMIT → delete cart key.

If any product is out of stock the whole transaction rolls back and the
cart is untouched. Redis cart is only deleted after a successful commit.
"""

import json
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from core.exceptions import (
    CartEmptyError,
    InsufficientStockError,
    OrderNotFoundError,
    StorefrontNotFoundError,
)
from core.redis_client import get_redis
from db.models import InventoryTransaction, Order, OrderItem, Product
from orders.models import OrderItemResponse, OrderListResponse, OrderResponse
from orders.repository import OrderRepository
from storefront.repository import StorefrontRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.orders")


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = OrderRepository(db)

    def create_from_cart(self, consumer_id: str, slug: str) -> OrderResponse:
        """
        Convert the consumer's cart for `slug` into a confirmed order.

        Steps (all inside one DB transaction):
          1. Validate storefront exists and is public.
          2. Load cart from Redis — raise CartEmptyError if empty.
          3. For each item: SELECT FOR UPDATE, validate stock, decrement,
             record InventoryTransaction.
          4. Create Order + OrderItems.
          5. COMMIT.
          6. Delete Redis cart key.
        """
        tenant = StorefrontRepository(self.db).get_public_tenant_by_slug(slug)
        if tenant is None:
            raise StorefrontNotFoundError(slug)

        r = get_redis()
        cart_key = f"cart:{consumer_id}:{slug}"
        raw = r.get(cart_key) if r else None
        cart: dict = json.loads(raw) if raw else {}

        if not cart:
            raise CartEmptyError("El carrito está vacío")

        total = Decimal("0.00")
        items_data = []

        for product_id, entry in cart.items():
            quantity = entry["quantity"]

            product = (
                self.db.query(Product)
                .filter(
                    Product.id == product_id,
                    Product.tenant_id == tenant.id,
                    Product.is_deleted.is_(False),
                )
                .with_for_update()  # row-level lock — prevents overselling
                .first()
            )
            if product is None:
                raise StorefrontNotFoundError(f"Producto '{product_id}' ya no está disponible")

            if product.inventory_count < quantity:
                raise InsufficientStockError(
                    f"Stock insuficiente para '{product.name}': "
                    f"disponible {product.inventory_count}, solicitado {quantity}"
                )

            before = product.inventory_count
            product.inventory_count -= quantity

            self.db.add(InventoryTransaction(
                product_id=product.id,
                tenant_id=tenant.id,
                action="sell",
                quantity_change=-quantity,
                quantity_before=before,
                quantity_after=product.inventory_count,
                notes="Pedido de cliente B2C",
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

        self.db.commit()
        logger.info("Order created: order_id=%s consumer=%s tenant=%s total=%s",
                    order.id, consumer_id, tenant.id, total)

        # Clear cart only after successful commit
        if r:
            r.delete(cart_key)

        return self._to_response(order, items_data, slug, tenant.business_name)

    def get_order(self, consumer_id: str, order_id: str) -> OrderResponse:
        order = self.repo.get_by_id(order_id, consumer_id)
        if order is None:
            raise OrderNotFoundError(order_id)
        return self._order_to_response(order)

    def list_orders(self, consumer_id: str, page: int, page_size: int) -> OrderListResponse:
        orders, total = self.repo.list_by_consumer(consumer_id, page, page_size)
        return OrderListResponse(
            items=[self._order_to_response(o) for o in orders],
            total=total,
            page=page,
            page_size=page_size,
        )

    @staticmethod
    def _to_response(order: Order, items_data: list, slug: str, business_name: str) -> OrderResponse:
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
            created_at=order.created_at,
        )
