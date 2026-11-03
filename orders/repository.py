"""Data-access layer for the orders module."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session, joinedload

from db.models import Consumer, Order, OrderItem


class OrderRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, order_id: str, consumer_id: str) -> Optional[Order]:
        """
        Fetch a single order. consumer_id guard ensures a consumer can
        never see another consumer's order — returns None (→ 404) instead
        of 403 to avoid leaking existence.
        """
        return (
            self.db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.tenant))
            .filter(Order.id == order_id, Order.consumer_id == consumer_id)
            .first()
        )

    # ── Cobro idempotente ─────────────────────────────────────────────

    def lock_idempotency_key(self, tenant_id: str, key: str) -> None:
        """
        Serializa las peticiones con la misma llave dentro de un negocio.

        Sin esto, dos peticiones idénticas que llegan juntas (doble clic) pasan
        a la vez la búsqueda "¿ya existe?", la segunda espera a que la primera
        suelte las existencias y recién entonces choca con el índice único. Con
        el candado, la segunda espera ANTES de analizar nada y al despertar ya
        encuentra la venta de la primera. Se suelta solo al terminar la
        transacción (commit o rollback).
        """
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"order-idem:{tenant_id}:{key}"},
        )

    def get_by_idempotency_key(self, tenant_id: str, key: str) -> Optional[Order]:
        """La venta de este negocio que ya se guardó con esa llave, si existe."""
        return (
            self.db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.tenant))
            .filter(Order.tenant_id == tenant_id, Order.idempotency_key == key)
            .first()
        )

    def list_by_consumer(
        self, consumer_id: str, page: int, page_size: int
    ) -> tuple[list[Order], int]:
        q = (
            self.db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.tenant))
            .filter(Order.consumer_id == consumer_id)
        )
        total = q.count()
        items = (
            q.order_by(Order.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    # ── Vista del negocio (ventas) ────────────────────────────────────

    def get_by_id_for_tenant(self, order_id: str, tenant_id: str) -> Optional[Order]:
        """
        Fetch one of *this tenant's* orders. The tenant_id guard is what stops
        a business from touching another business's order — returns None
        (→ 404) rather than 403, same as the consumer-side guard.
        """
        return (
            self.db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.tenant))
            .filter(Order.id == order_id, Order.tenant_id == tenant_id)
            .first()
        )

    def _sales_query(
        self,
        tenant_id: str,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        status: Optional[str],
        search: Optional[str],
    ):
        """
        Base query for a tenant's sales, with the report filters applied.

        The tenant_id filter is not optional — it is what keeps one business
        from reading another's sales (Rules.md section 2.6).

        El JOIN con `consumers` es EXTERNO a propósito: una venta de mostrador
        no tiene cliente registrado (`consumer_id` nulo desde la migración 005).
        Con un join interno, esas ventas desaparecerían del reporte y, peor,
        de los totales — el negocio vería ingresos menores a los reales sin
        ninguna señal de que faltan filas.
        """
        q = (
            self.db.query(Order)
            .outerjoin(Consumer, Consumer.id == Order.consumer_id)
            .filter(Order.tenant_id == tenant_id)
        )

        if date_from is not None:
            q = q.filter(Order.created_at >= date_from)
        if date_to is not None:
            q = q.filter(Order.created_at < date_to)
        if status:
            q = q.filter(Order.status == status)
        if search:
            like = f"%{search.strip()}%"
            q = q.filter(
                or_(
                    Order.id.ilike(like),
                    Consumer.name.ilike(like),
                    Consumer.email.ilike(like),
                    # El nombre capturado en el mostrador también es buscable;
                    # si no, las ventas sin cuenta solo se encontrarían por folio.
                    Order.customer_name.ilike(like),
                )
            )
        return q

    def list_by_tenant(
        self,
        tenant_id: str,
        page: int,
        page_size: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[tuple[Order, Consumer]], int]:
        """Return `(order, consumer)` pairs for one page, newest first."""
        q = self._sales_query(tenant_id, date_from, date_to, status, search)
        total = q.count()
        rows = (
            q.options(joinedload(Order.items), joinedload(Order.tenant))
            .add_entity(Consumer)
            .order_by(Order.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return rows, total

    def summarize_by_tenant(
        self,
        tenant_id: str,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """
        Totals over the *whole* filtered set, not just the current page —
        a report footer that only added up the visible rows would be wrong.

        Canceled orders are counted separately and excluded from revenue.
        """
        # Cuatro de los totales salen de UNA consulta con agregados condicionales
        # (`FILTER`): antes eran cuatro consultas separadas sobre el mismo
        # conjunto, y cada una cuesta un viaje a la base.
        no_cancelada = Order.status != "canceled"
        order_count, canceled_count, revenue, shipping, tips, discounts = (
            self._sales_query(tenant_id, date_from, date_to, status, search)
            .with_entities(
                func.count(Order.id),
                func.count(Order.id).filter(Order.status == "canceled"),
                func.coalesce(func.sum(Order.total).filter(no_cancelada), 0),
                # Cuánto del ingreso fue logística y no catálogo. El reporte lo
                # resta para no presentar el envío como venta de producto.
                func.coalesce(func.sum(Order.shipping_cost).filter(no_cancelada), 0),
                # Las propinas están dentro del ingreso, pero no son venta de
                # producto: se reportan aparte por la misma razón.
                func.coalesce(func.sum(Order.tip_amount).filter(no_cancelada), 0),
                func.coalesce(func.sum(Order.discount_amount).filter(no_cancelada), 0),
            )
            .one()
        )

        # Las unidades necesitan unir con las partidas, lo que multiplicaría
        # las filas de los agregados de arriba: se quedan en su propia consulta.
        units = (
            self._sales_query(tenant_id, date_from, date_to, status, search)
            .filter(no_cancelada)
            .join(OrderItem, OrderItem.order_id == Order.id)
            .with_entities(func.coalesce(func.sum(OrderItem.quantity), 0))
            .scalar()
            or 0
        )

        return {
            "order_count": int(order_count or 0),
            "canceled_count": int(canceled_count or 0),
            "revenue": Decimal(revenue),
            "units": int(units),
            "shipping": Decimal(shipping),
            "tips": Decimal(tips),
            "discounts": Decimal(discounts),
        }
