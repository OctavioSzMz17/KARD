"""
Acceso a datos de la caja (Repository Pattern).

Solo consultas: ninguna regla de negocio y ningún commit. Todo está acotado por
`tenant_id`: el turno de otro negocio es tan invisible como uno inexistente.
"""

from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import CashSession, Order, User

# Ventas que cuentan para el corte. `pending` aún no se cobró; `canceled` y
# `refunded` devolvieron el dinero; `fulfilled` es el valor histórico de "entregada".
COUNTED_STATUSES = ("paid", "fulfilled")
EXCLUDED_STATUSES = ("canceled", "refunded")


class CashRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_open(self, tenant_id: str, lock: Optional[str] = None) -> Optional[CashSession]:
        """
        El turno abierto del negocio, si lo hay.

        `lock="share"` lo bloquea para lectura compartida (una venta que se
        liga al turno) y `lock="update"` para escritura (el cierre). Juntos
        sirven de semáforo: un cierre espera a que termine la venta en curso,
        y una venta posterior ya no encuentra turno abierto.
        """
        q = self.db.query(CashSession).filter(
            CashSession.tenant_id == tenant_id, CashSession.status == "open"
        )
        if lock == "share":
            q = q.with_for_update(read=True)
        elif lock == "update":
            q = q.with_for_update()
        return q.first()

    def get_by_id(self, tenant_id: str, session_id: str) -> Optional[CashSession]:
        return (
            self.db.query(CashSession)
            .filter(CashSession.tenant_id == tenant_id, CashSession.id == session_id)
            .first()
        )

    def list_for_tenant(self, tenant_id: str, page: int, page_size: int) -> tuple[list[CashSession], int]:
        q = self.db.query(CashSession).filter(CashSession.tenant_id == tenant_id)
        total = q.count()
        items = (
            q.order_by(CashSession.opened_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def create(self, session: CashSession) -> CashSession:
        self.db.add(session)
        self.db.flush()   # saca a la luz la violación del índice de "una sola caja abierta"
        return session

    def save(self, session: CashSession) -> CashSession:
        self.db.add(session)
        self.db.flush()
        return session

    def sales_aggregates(self, session_id: str) -> dict:
        """
        Totales de las ventas del turno, en una sola ida a la base:
        por método de pago (solo vigentes), descuentos, propinas y cuántas
        ventas quedaron fuera por estar canceladas o reembolsadas.
        """
        rows = (
            self.db.query(
                Order.payment_method,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
                func.coalesce(func.sum(Order.discount_amount), 0),
                func.coalesce(func.sum(Order.tip_amount), 0),
            )
            .filter(Order.cash_session_id == session_id, Order.status.in_(COUNTED_STATUSES))
            .group_by(Order.payment_method)
            .all()
        )
        excluded = (
            self.db.query(func.count(Order.id))
            .filter(Order.cash_session_id == session_id, Order.status.in_(EXCLUDED_STATUSES))
            .scalar()
            or 0
        )
        # Propinas pagadas en efectivo: el subconjunto que sí está en el cajón.
        tips_cash = (
            self.db.query(func.coalesce(func.sum(Order.tip_amount), 0))
            .filter(
                Order.cash_session_id == session_id,
                Order.status.in_(COUNTED_STATUSES),
                Order.payment_method == "cash",
            )
            .scalar()
            or 0
        )
        return {
            "rows": [
                {
                    "method": r[0],
                    "count": int(r[1]),
                    "total": Decimal(r[2]),
                    "discounts": Decimal(r[3]),
                    "tips": Decimal(r[4]),
                }
                for r in rows
            ],
            "excluded": int(excluded),
            "tips_cash": Decimal(tips_cash),
        }

    def emails(self, user_ids: list[str]) -> dict[str, str]:
        """Correo de cada usuario, para mostrar quién abrió y cerró un turno."""
        ids = [i for i in set(user_ids) if i]
        if not ids:
            return {}
        return {u.id: u.email for u in self.db.query(User).filter(User.id.in_(ids)).all()}
