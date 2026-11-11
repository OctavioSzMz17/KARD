"""
Lógica de ventas. Un mismo pedido cubre los dos canales del producto:

  · Marketplace (B2C) — el cliente arma su carrito en Redis y hace checkout.
  · Punto de venta (B2B) — un empleado captura la venta en el mostrador, sin
    carrito y sin cuenta de cliente.

Ambos terminan en la misma tabla y comparten la parte difícil: el análisis
bajo bloqueo de fila y el descuento en cascada de insumos.

Flujo (idéntico para los dos canales):
  1. Analiza cada artículo bajo bloqueo de fila (SELECT FOR UPDATE): valida su
     propio stock y el de cada insumo de su receta.
  2. Si algo no alcanza:
       - on_insufficient_stock="reject" (default): rollback y OrderStockConflictError
         → 409 con el detalle estructurado para que el front pregunte al usuario.
       - on_insufficient_stock="skip": omite esos productos y sigue con el resto,
         reportándolos en `skipped`. Si no queda nada, 409 (all_skipped).
  3. Descuenta stock del final y de cada insumo, registra inventory_transactions,
     crea Order + OrderItems y COMMIT. El carrito de Redis se borra DESPUÉS del
     commit (solo en el canal B2C): Redis y Postgres no comparten transacción,
     así que el único orden seguro es confirmar primero lo permanente.
"""

import json
import logging
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cash.repository import CashRepository
from core.exceptions import (
    OrderCanceledError,
    CartEmptyError,
    CashClosedError,
    IdempotencyConflictError,
    InvalidMetadataError,
    OrderNotFoundError,
    OrderStockConflictError,
    ProductNotFoundError,
    StorefrontNotFoundError,
)
from core.redis_client import get_redis
from core.tenant_config import read_fulfillment
from db.models import Consumer, InventoryTransaction, Order, OrderItem, Product, ProductRecipe, Tenant, User
from datetime import datetime, timedelta, timezone

from orders.cashier import full_name as cashier_full_name
from orders.idempotency import request_fingerprint
from orders.models import (
    CreateOrderRequest,
    CreatePosSaleRequest,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    OrdersQueueResponse,
    SaleResponse,
    SalesReportResponse,
    SalesStatsResponse,
    SalesSummary,
    SkippedItemResponse,
)
from orders.money import ZERO, change_for, discount_amount, order_total, to_money
from orders.stats import average_ticket, fill_days, local_today, window_start_utc
from orders.queue import is_canceled, queue_state
from orders.repository import ACTIVE_FULFILLMENT, OrderRepository
from products.stock_policy import tracks_stock
from storefront.repository import StorefrontRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.orders")

# Etiqueta para ventas sin cuenta de cliente, cuando el empleado tampoco
# capturó un nombre. Mejor una etiqueta honesta que un campo vacío.
WALK_IN_LABEL = "Venta de mostrador"


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = OrderRepository(db)

    def create_from_cart(
        self,
        consumer_id: str,
        payload: CreateOrderRequest,
    ) -> OrderResponse:
        """Checkout del marketplace: el carrito de Redis del cliente se vuelve pedido."""
        slug = payload.slug
        tenant = StorefrontRepository(self.db).get_public_tenant_by_slug(slug)
        if tenant is None:
            raise StorefrontNotFoundError(slug)

        self._assert_offered(tenant, payload.fulfillment_type)

        r = get_redis()
        cart_key = f"cart:{consumer_id}:{slug}"
        raw = r.get(cart_key) if r else None
        cart: dict = json.loads(raw) if raw else {}

        if not cart:
            raise CartEmptyError("El carrito está vacío")

        fulfillable, unavailable = self._analyze_cart(tenant.id, cart)

        if unavailable:
            if payload.on_insufficient_stock != "skip":
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

        order, items_data = self._apply(
            tenant,
            fulfillable,
            consumer_id=consumer_id,
            captured_by="self",
            fulfillment_type=payload.fulfillment_type,
            # El marketplace cobra después (o contra entrega): el pedido nace
            # pendiente de pago, aunque ya esté preparándose.
            status="pending",
            payment_method=None,
            table_number=payload.table_number,
            shipping_address=payload.shipping_address.model_dump() if payload.shipping_address else None,
        )

        self.db.commit()
        logger.info(
            "Order created: order_id=%s consumer=%s tenant=%s type=%s total=%s skipped=%d",
            order.id, consumer_id, tenant.id, payload.fulfillment_type, order.total, len(skipped),
        )

        # Clear cart only after successful commit
        if r:
            r.delete(cart_key)

        return self._to_response(order, items_data, slug, tenant.business_name, skipped)

    # ------------------------------------------------------------------
    # Punto de venta — el empleado captura la venta en el mostrador
    # ------------------------------------------------------------------
    def create_pos_sale(
        self,
        tenant_id: str,
        user_id: str,
        payload: CreatePosSaleRequest,
        cashier: str | None = None,
        idempotency_key: str | None = None,
        register_name: str | None = None,
    ) -> OrderResponse:
        """Cobra y devuelve la venta; ver `create_pos_sale_idempotent`."""
        return self.create_pos_sale_idempotent(
            tenant_id, user_id, payload, cashier, idempotency_key, register_name
        )[0]

    def create_pos_sale_idempotent(
        self,
        tenant_id: str,
        user_id: str,
        payload: CreatePosSaleRequest,
        cashier: str | None = None,
        idempotency_key: str | None = None,
        register_name: str | None = None,
    ) -> tuple[OrderResponse, bool]:
        """
        Venta capturada por un empleado del negocio. Devuelve `(venta, repetida)`.

        Con `idempotency_key`, cobrar el mismo ticket dos veces (doble clic,
        reintento tras perder la señal, respuesta que no llegó) NO crea otra
        venta ni vuelve a descontar inventario: la segunda vez devuelve la que
        ya se guardó, con `repetida=True`. La misma llave con otro contenido
        es un error (`IdempotencyConflictError`).

        Exige la caja abierta: la venta se liga al turno para que el corte
        cuadre. El turno se toma con bloqueo compartido, así un cierre en curso
        espera a que esta venta termine y la incluya en su cuenta.

        Reusa íntegro el motor del checkout —bloqueo de filas, cascada de
        insumos y bitácora atómica— alimentándolo con la lista de artículos
        del cuerpo en vez del carrito de Redis.

        El mostrador cobra en el momento, así que el pago nace saldado.
        """
        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise StorefrontNotFoundError(tenant_id)

        # Primero la llave, antes que la caja y el stock: un reintento de una
        # venta ya hecha debe contestar lo mismo aunque la caja ya se haya
        # cerrado o el inventario ya no alcance para repetirla.
        fingerprint = request_fingerprint(payload, user_id) if idempotency_key else None
        if idempotency_key:
            self.repo.lock_idempotency_key(tenant_id, idempotency_key)
            previous = self.repo.get_by_idempotency_key(tenant_id, idempotency_key)
            if previous is not None:
                return self._replay(previous, fingerprint, cashier), True

        cash_session = CashRepository(self.db).get_open(tenant_id, lock="share")
        if cash_session is None:
            raise CashClosedError(tenant_id)

        self._assert_offered(tenant, payload.fulfillment_type)

        # Las cantidades repetidas del mismo producto se suman: el analizador
        # bloquea cada fila una sola vez, y dos líneas sueltas del mismo
        # artículo validarían su stock por separado y dejarían pasar de más.
        merged: dict[str, int] = {}
        for item in payload.items:
            merged[item.product_id] = merged.get(item.product_id, 0) + item.quantity

        cart_like = {pid: {"quantity": qty} for pid, qty in merged.items()}
        fulfillable, unavailable = self._analyze_cart(tenant_id, cart_like)

        if unavailable:
            if payload.on_insufficient_stock != "skip":
                self.db.rollback()
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

        # Descuento, propina y cambio se validan ANTES de mover el inventario:
        # un descuento mal capturado no debe dejar existencias descontadas.
        try:
            subtotal = sum((p["product"].price * p["quantity"] for p in fulfillable), Decimal("0.00"))
            discount = discount_amount(
                subtotal,
                payload.discount.type if payload.discount else None,
                payload.discount.value if payload.discount else None,
            )
            shipping = self._shipping_cost_for(tenant, payload.fulfillment_type, subtotal)
            total = order_total(subtotal, discount, shipping, to_money(payload.tip))
            change = change_for(total, payload.amount_received) if payload.amount_received is not None else None
        except Exception:
            self.db.rollback()
            raise

        try:
            order, items_data = self._apply(
                tenant,
                fulfillable,
                user_id=user_id,
                captured_by="staff",
                fulfillment_type=payload.fulfillment_type,
                status="paid",
                payment_method=payload.payment_method,
                table_number=payload.table_number,
                shipping_address=payload.shipping_address.model_dump() if payload.shipping_address else None,
                customer_name=payload.customer_name,
                discount=discount,
                tip=to_money(payload.tip),
                amount_received=to_money(payload.amount_received) if payload.amount_received is not None else None,
                change_given=change,
                cash_session_id=cash_session.id,
                idempotency_key=idempotency_key,
                request_hash=fingerprint,
                register_name=register_name,
            )
            self.db.commit()
        except IntegrityError:
            # Red de seguridad: el candado de arriba ya serializa las llaves
            # repetidas, pero si dos peticiones llegaran a competir por el
            # índice único, la que pierde devuelve la venta de la ganadora.
            self.db.rollback()
            previous = (
                self.repo.get_by_idempotency_key(tenant_id, idempotency_key)
                if idempotency_key else None
            )
            if previous is None:
                raise
            return self._replay(previous, fingerprint, cashier), True
        logger.info(
            "POS sale: order_id=%s tenant=%s user=%s type=%s pay=%s total=%s skipped=%d",
            order.id, tenant_id, user_id, payload.fulfillment_type,
            payload.payment_method, order.total, len(skipped),
        )

        return self._to_response(order, items_data, tenant.slug or "", tenant.business_name, skipped, cashier), False

    def _replay(self, previous: Order, fingerprint: str | None, cashier: str | None) -> OrderResponse:
        """
        La venta que ya se guardó con esta llave, si la petición es la misma.

        La huella incluye quién cobra y todo el contenido: otra persona o un
        ticket distinto con la misma llave no recibe la venta ajena.
        """
        if previous.request_hash != fingerprint:
            raise IdempotencyConflictError(previous.id)
        logger.info("POS sale replayed: order_id=%s", previous.id)
        return self._order_to_response(previous).model_copy(update={"cashier": cashier})

    # ------------------------------------------------------------------
    # Reglas de entrega del negocio
    # ------------------------------------------------------------------
    @staticmethod
    def _assert_offered(tenant: Tenant, fulfillment_type: str) -> None:
        """
        Un negocio solo acepta los modos de entrega que tiene habilitados.

        Sin esto, un cliente podría pedir envío a una óptica que no reparte, o
        consumo en mesa en un negocio sin local. La configuración vive en el
        JSONB del tenant, así que no hubo que migrar nada para soportarlo.
        """
        if not read_fulfillment(tenant.config).get(fulfillment_type, False):
            raise InvalidMetadataError(
                f"Este negocio no ofrece '{fulfillment_type}' como modo de entrega"
            )

    @staticmethod
    def _shipping_cost_for(tenant: Tenant, fulfillment_type: str, subtotal: Decimal) -> Decimal:
        """
        Costo de envío según la configuración del negocio.

        Solo los envíos cobran: consumir en la mesa, llevárselo o recogerlo no
        tienen costo logístico. Si el subtotal alcanza el mínimo de envío
        gratis, el cargo se perdona.
        """
        if fulfillment_type != "delivery":
            return Decimal("0.00")

        cfg = read_fulfillment(tenant.config)
        fee = Decimal(cfg.get("shipping_flat_fee") or "0.00")

        free_above = cfg.get("free_shipping_above")
        if free_above is not None and subtotal >= Decimal(free_above):
            return Decimal("0.00")

        return fee

    # ------------------------------------------------------------------
    # Phase 1 — analysis (row-locks every final + insumo involved)
    # ------------------------------------------------------------------
    def _business_type_of(self, tenant_id: str) -> str | None:
        """Tipo del negocio (restaurante, óptica…): de él depende qué productos llevan stock."""
        return self.db.query(Tenant.business_type).filter(Tenant.id == tenant_id).scalar()

    def _analyze_cart(self, tenant_id: str, cart: dict) -> tuple[list[dict], list[dict]]:
        fulfillable: list[dict] = []
        unavailable: list[dict] = []
        business_type = self._business_type_of(tenant_id)

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

            # Un platillo no tiene existencias propias: lo único que puede faltar
            # son los insumos de su receta (products/stock_policy.py).
            counts_stock = tracks_stock(business_type, product.product_type)
            product_short = counts_stock and product.inventory_count < quantity
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
                "tracks_stock": counts_stock,
            })

        return fulfillable, unavailable

    # ------------------------------------------------------------------
    # Phase 2 — apply (decrement stock + write order). No commit here.
    # ------------------------------------------------------------------
    def _apply(
        self,
        tenant,
        fulfillable: list[dict],
        *,
        captured_by: str,
        fulfillment_type: str,
        status: str,
        consumer_id: str | None = None,
        user_id: str | None = None,
        payment_method: str | None = None,
        table_number: str | None = None,
        shipping_address: dict | None = None,
        customer_name: str | None = None,
        discount: Decimal = Decimal("0.00"),
        tip: Decimal = Decimal("0.00"),
        amount_received: Decimal | None = None,
        change_given: Decimal | None = None,
        cash_session_id: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        register_name: str | None = None,
    ) -> tuple[Order, list[dict]]:
        total = Decimal("0.00")
        items_data: list[dict] = []

        for plan in fulfillable:
            product = plan["product"]
            quantity = plan["quantity"]

            if plan.get("tracks_stock", True):
                before = product.inventory_count
                product.inventory_count = before - quantity
                self.db.add(InventoryTransaction(
                    product_id=product.id,
                    tenant_id=tenant.id,
                    action="sell",
                    quantity_change=-quantity,
                    quantity_before=before,
                    quantity_after=product.inventory_count,
                    user_id=user_id,
                    notes=f"Venta {'mostrador' if captured_by == 'staff' else 'en línea'} (producto final)",
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
                    user_id=user_id,
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

        # `total` acumuló el subtotal de los artículos; el envío se suma aparte
        # para que el reporte pueda distinguir producto de logística.
        subtotal = total
        shipping_cost = self._shipping_cost_for(tenant, fulfillment_type, subtotal)

        order = Order(
            id=generate_id("ord", 20),
            consumer_id=consumer_id,
            tenant_id=tenant.id,
            user_id=user_id,
            captured_by=captured_by,
            fulfillment_type=fulfillment_type,
            fulfillment_status="preparing",
            status=status,
            payment_method=payment_method,
            subtotal=subtotal,
            shipping_cost=shipping_cost,
            discount_amount=discount,
            tip_amount=tip,
            total=order_total(subtotal, discount, shipping_cost, tip),
            amount_received=amount_received,
            change_given=change_given,
            cash_session_id=cash_session_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            register_name=register_name,
            table_number=table_number,
            shipping_address=shipping_address,
            customer_name=customer_name,
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
            items=[self._sale_to_response(order, consumer, user) for order, consumer, user in rows],
            summary=SalesSummary(**summary),
            total=total,
            page=page,
            page_size=page_size,
        )

    def _return_stock(self, order: Order, user_id: str | None) -> None:
        """
        Devuelve al inventario lo que descontó la venta de este pedido: los insumos de la receta de
        cada partida y, en lo que lleva existencias propias, el producto mismo. Cada devolución deja
        su movimiento en la bitácora. No hace commit: va en la misma transacción que la cancelación.

        Se calcula con la receta de hoy: si cambió desde la venta, la devolución es aproximada.
        """
        business_type = self._business_type_of(order.tenant_id)
        note = f"Devolución por cancelar el pedido {order.id}"

        def give_back(product: Product, quantity: int, why: str) -> None:
            before = product.inventory_count
            product.inventory_count = before + quantity
            self.db.add(InventoryTransaction(
                product_id=product.id,
                tenant_id=order.tenant_id,
                action="restock",
                quantity_change=quantity,
                quantity_before=before,
                quantity_after=product.inventory_count,
                user_id=user_id,
                notes=why,
            ))

        for item in order.items:
            # Sin filtrar por is_deleted: un producto dado de baja después también recupera lo suyo.
            product = (
                self.db.query(Product)
                .filter(Product.id == item.product_id, Product.tenant_id == order.tenant_id)
                .with_for_update()
                .first()
            )
            if product is None:
                continue

            if tracks_stock(business_type, product.product_type):
                give_back(product, item.quantity, f"{note} (producto final)")

            recipes = (
                self.db.query(ProductRecipe)
                .filter(
                    ProductRecipe.final_product_id == product.id,
                    ProductRecipe.tenant_id == order.tenant_id,
                )
                .all()
            )
            for rec in recipes:
                insumo = (
                    self.db.query(Product)
                    .filter(Product.id == rec.insumo_product_id, Product.tenant_id == order.tenant_id)
                    .with_for_update()
                    .first()
                )
                if insumo is not None:
                    give_back(insumo, rec.quantity * item.quantity, f"{note} ('{product.name}')")

    def online_queue(self, tenant_id: str, closed_hours: int = 24) -> OrdersQueueResponse:
        """El tablero de órdenes: lo que sigue en marcha y lo cerrado en las últimas horas."""
        closed_since = datetime.now(timezone.utc) - timedelta(hours=closed_hours)
        rows = self.repo.list_online_queue(tenant_id, closed_since)
        items = [self._sale_to_response(order, consumer, user) for order, consumer, user in rows]

        counts = {"preparing": 0, "ready": 0, "in_transit": 0, "completed": 0, "canceled": 0}
        awaiting_payment = 0
        for sale in items:
            state = queue_state(sale.status, sale.fulfillment_status)
            counts[state] = counts.get(state, 0) + 1
            if state in ACTIVE_FULFILLMENT and sale.status == "pending":
                awaiting_payment += 1

        return OrdersQueueResponse(items=items, counts=counts, awaiting_payment=awaiting_payment)

    def stats(self, tenant_id: str, days: int, tz_offset_minutes: int) -> SalesStatsResponse:
        """
        Ventas cobradas de los últimos `days` días (el de hoy incluido) y lo más vendido, para el
        Dashboard. Los días son los del negocio: se cuentan con el desfase del navegador.
        """
        today = local_today(datetime.now(timezone.utc), tz_offset_minutes)
        since = window_start_utc(today, days, tz_offset_minutes)

        per_day = self.repo.daily_sales(tenant_id, since, tz_offset_minutes)
        filled = fill_days(per_day, today, days)
        revenue = sum((row["revenue"] for row in filled), ZERO)
        orders = sum(row["orders"] for row in filled)

        return SalesStatsResponse(
            days=filled,
            top_products=self.repo.top_products(tenant_id, since),
            total_revenue=to_money(revenue),
            total_orders=orders,
            average_ticket=average_ticket(revenue, orders),
        )

    def update_sale_status(
        self,
        tenant_id: str,
        order_id: str,
        new_status: str | None = None,
        new_fulfillment_status: str | None = None,
        user_id: str | None = None,
    ) -> SaleResponse:
        """
        Mueve el estado de pago, el de entrega, o ambos.

        Son ejes independientes: cobrar una mesa no la sirve, y servirla no
        la cobra. El validador del request ya garantiza que venga al menos uno.

        Cancelar un pedido devuelve al inventario lo que se descontó al venderlo, una sola vez. Un
        cancelado no se reabre: volver a descontar podría no alcanzar, y los cancelados de antes
        de esta regla nunca devolvieron nada, así que reabrirlos descontaría de más.
        """
        order = self.repo.get_by_id_for_tenant(order_id, tenant_id, lock=True)
        if order is None:
            raise OrderNotFoundError("Pedido no encontrado")

        was_canceled = is_canceled(order.status, order.fulfillment_status)
        will_cancel = is_canceled(
            new_status if new_status is not None else order.status,
            new_fulfillment_status if new_fulfillment_status is not None else order.fulfillment_status,
        )
        # Ningún eje puede salir de "cancelado": si no, quedaría un pedido que el tablero ve cancelado
        # y el reporte cuenta como venta (o al revés).
        reopens_payment = order.status == "canceled" and new_status not in (None, "canceled")
        reopens_delivery = order.fulfillment_status == "canceled" and new_fulfillment_status not in (None, "canceled")
        if reopens_payment or reopens_delivery or (was_canceled and not will_cancel):
            raise OrderCanceledError(order_id)
        if will_cancel and not was_canceled:
            self._return_stock(order, user_id)

        if new_status is not None:
            order.status = new_status
        if new_fulfillment_status is not None:
            order.fulfillment_status = new_fulfillment_status

        self.db.commit()
        self.db.refresh(order)

        # Una venta de mostrador no tiene cliente registrado.
        consumer = (
            self.db.query(Consumer).filter(Consumer.id == order.consumer_id).first()
            if order.consumer_id
            else None
        )
        user = (
            self.db.query(User).filter(User.id == order.user_id).first()
            if order.user_id
            else None
        )
        return self._sale_to_response(order, consumer, user)

    @staticmethod
    def _sale_to_response(order: Order, consumer, user=None) -> SaleResponse:
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

        # Tres orígenes posibles para el nombre, en orden de preferencia: la
        # cuenta del cliente, lo que capturó el empleado, o una etiqueta
        # genérica. Una venta de mostrador no tiene correo que mostrar.
        if consumer is not None:
            display_name = (consumer.name or "").strip() or consumer.email
            email = consumer.email
        else:
            display_name = (order.customer_name or "").strip() or WALK_IN_LABEL
            email = None

        return SaleResponse(
            order_id=order.id,
            status=order.status,
            fulfillment_type=order.fulfillment_type,
            fulfillment_status=order.fulfillment_status,
            captured_by=order.captured_by,
            payment_method=order.payment_method,
            subtotal=order.subtotal,
            shipping_cost=order.shipping_cost,
            discount_amount=order.discount_amount,
            tip_amount=order.tip_amount,
            amount_received=order.amount_received,
            change_given=order.change_given,
            total=order.total,
            item_count=sum(i.quantity for i in items),
            customer_name=display_name,
            customer_email=email,
            table_number=order.table_number,
            shipping_address=getattr(order, "shipping_address", None),
            items=items,
            created_at=order.created_at,
            cashier_name=cashier_full_name(user.full_name, user.email) if user is not None else None,
            cashier_code=user.employee_code if user is not None else None,
            register_name=getattr(order, "register_name", None),
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
    def _to_response(order, items_data, slug, business_name, skipped=None, cashier=None) -> OrderResponse:
        return OrderResponse(
            order_id=order.id,
            slug=slug,
            business_name=business_name,
            status=order.status,
            fulfillment_type=order.fulfillment_type,
            fulfillment_status=order.fulfillment_status,
            subtotal=order.subtotal,
            shipping_cost=order.shipping_cost,
            total=order.total,
            table_number=order.table_number,
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
            payment_method=order.payment_method,
            discount_amount=order.discount_amount,
            tip_amount=order.tip_amount,
            amount_received=order.amount_received,
            change_given=order.change_given,
            customer_name=order.customer_name,
            cashier=cashier,
            register_name=getattr(order, "register_name", None),
        )

    @staticmethod
    def _order_to_response(order: Order) -> OrderResponse:
        return OrderResponse(
            order_id=order.id,
            slug=order.tenant.slug or "",
            business_name=order.tenant.business_name,
            status=order.status,
            fulfillment_type=order.fulfillment_type,
            fulfillment_status=order.fulfillment_status,
            subtotal=order.subtotal,
            shipping_cost=order.shipping_cost,
            total=order.total,
            table_number=order.table_number,
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
            payment_method=order.payment_method,
            discount_amount=order.discount_amount,
            tip_amount=order.tip_amount,
            amount_received=order.amount_received,
            change_given=order.change_given,
            customer_name=order.customer_name,
            register_name=getattr(order, "register_name", None),
        )
