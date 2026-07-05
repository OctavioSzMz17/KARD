"""
Cart business logic — Redis-backed, per consumer per storefront.

Key schema:  cart:{consumer_id}:{slug}
Value:       JSON dict  { product_id: { quantity, price_snapshot, name } }
TTL:         7 days, refreshed on every write.

If Redis is unavailable the service raises CartUnavailableError (→ 503).
Cart operations are intentionally strict about Redis availability — unlike
the catalog cache, the cart IS Redis; there is no DB fallback.
"""

import json
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from cart.models import AddToCartRequest, CartItemResponse, CartResponse
from core.exceptions import CartUnavailableError, ProductNotFoundError, StorefrontNotFoundError
from core.redis_client import get_redis
from db.models import Product
from storefront.repository import StorefrontRepository

logger = logging.getLogger("kard.cart")

_CART_TTL = 7 * 24 * 3600  # 7 days in seconds


class CartService:
    def __init__(self, db: Session):
        self.db = db
        self._sf_repo = StorefrontRepository(db)

    @staticmethod
    def _key(consumer_id: str, slug: str) -> str:
        return f"cart:{consumer_id}:{slug}"

    def _redis(self):
        r = get_redis()
        if r is None:
            raise CartUnavailableError("Cart service temporarily unavailable")
        return r

    def _get_tenant(self, slug: str):
        tenant = self._sf_repo.get_public_tenant_by_slug(slug)
        if tenant is None:
            raise StorefrontNotFoundError(slug)
        return tenant

    def _load(self, r, key: str) -> dict:
        raw = r.get(key)
        return json.loads(raw) if raw else {}

    def _save(self, r, key: str, data: dict) -> None:
        r.set(key, json.dumps(data), ex=_CART_TTL)

    def _build_response(self, cart: dict, slug: str, business_name: str) -> CartResponse:
        items = [
            CartItemResponse(
                product_id=pid,
                name=entry["name"],
                quantity=entry["quantity"],
                price_snapshot=Decimal(entry["price_snapshot"]),
                subtotal=Decimal(entry["price_snapshot"]) * entry["quantity"],
            )
            for pid, entry in cart.items()
        ]
        return CartResponse(
            slug=slug,
            business_name=business_name,
            items=items,
            total=sum(i.subtotal for i in items),
            item_count=sum(i.quantity for i in items),
        )

    def add_item(self, consumer_id: str, slug: str, payload: AddToCartRequest) -> CartResponse:
        """Add or update an item. quantity=0 removes the item."""
        tenant = self._get_tenant(slug)
        r = self._redis()

        product = (
            self.db.query(Product)
            .filter(
                Product.id == payload.product_id,
                Product.tenant_id == tenant.id,
                Product.product_type == "final",
                Product.is_deleted.is_(False),
            )
            .first()
        )
        if product is None:
            raise ProductNotFoundError(payload.product_id)

        key = self._key(consumer_id, slug)
        cart = self._load(r, key)

        if payload.quantity == 0:
            cart.pop(payload.product_id, None)
        else:
            cart[payload.product_id] = {
                "quantity": payload.quantity,
                "price_snapshot": str(product.price),
                "name": product.name,
            }

        self._save(r, key, cart)
        logger.debug("Cart updated: consumer=%s slug=%s items=%d", consumer_id, slug, len(cart))
        return self._build_response(cart, slug, tenant.business_name)

    def get_cart(self, consumer_id: str, slug: str) -> CartResponse:
        tenant = self._get_tenant(slug)
        r = self._redis()
        cart = self._load(r, self._key(consumer_id, slug))
        return self._build_response(cart, slug, tenant.business_name)

    def remove_item(self, consumer_id: str, slug: str, product_id: str) -> CartResponse:
        tenant = self._get_tenant(slug)
        r = self._redis()
        key = self._key(consumer_id, slug)
        cart = self._load(r, key)
        cart.pop(product_id, None)
        self._save(r, key, cart)
        return self._build_response(cart, slug, tenant.business_name)

    def clear(self, consumer_id: str, slug: str) -> None:
        self._get_tenant(slug)
        r = self._redis()
        r.delete(self._key(consumer_id, slug))
        logger.debug("Cart cleared: consumer=%s slug=%s", consumer_id, slug)
