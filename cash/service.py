"""
Lógica de negocio de la caja: abrir turno, cerrar con corte y consultarlos.

Qué entra al "efectivo esperado" (lo que debería haber en el cajón al cerrar):

    esperado = fondo inicial + ventas pagadas en EFECTIVO del turno

Las ventas con tarjeta o transferencia no tocan el cajón. Las propinas
cobradas en efectivo sí, y ya van dentro del total de esas ventas. El cambio
que se devolvió al cliente tampoco hay que restarlo: el total de la venta es lo
que realmente se quedó, no lo que el cliente entregó. Las ventas canceladas o
reembolsadas no cuentan: devolvieron el dinero.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit_logging.audit import log_operation
from cash.models import (
    CashSessionListResponse,
    CashSessionResponse,
    CashSummary,
    CloseCashRequest,
    MethodTotal,
    OpenCashRequest,
)
from cash.repository import CashRepository
from core.dependencies import CurrentUser
from core.exceptions import CashSessionAlreadyOpenError, CashSessionNotFoundError
from db.models import CashSession
from orders.money import ZERO, to_money
from utils.helpers import generate_id

logger = logging.getLogger("kard.cash")

_KNOWN_METHODS = ("cash", "card", "transfer")


def build_summary(aggregates: dict) -> CashSummary:
    """
    Convierte los agregados de la base en el resumen del turno.

    Aparte de la base para poder probarlo con números: es la cuenta que
    determina si el corte cuadra.
    """
    buckets = {name: {"count": 0, "total": ZERO} for name in (*_KNOWN_METHODS, "other")}
    discounts = ZERO
    tips_total = ZERO

    for row in aggregates["rows"]:
        name = row["method"] if row["method"] in _KNOWN_METHODS else "other"
        buckets[name]["count"] += row["count"]
        buckets[name]["total"] += row["total"]
        discounts += row["discounts"]
        tips_total += row["tips"]

    gross = sum((b["total"] for b in buckets.values()), ZERO)
    return CashSummary(
        sales_count=sum(b["count"] for b in buckets.values()),
        gross=to_money(gross),
        cash=MethodTotal(**{**buckets["cash"], "total": to_money(buckets["cash"]["total"])}),
        card=MethodTotal(**{**buckets["card"], "total": to_money(buckets["card"]["total"])}),
        transfer=MethodTotal(**{**buckets["transfer"], "total": to_money(buckets["transfer"]["total"])}),
        other=MethodTotal(**{**buckets["other"], "total": to_money(buckets["other"]["total"])}),
        discounts=to_money(discounts),
        tips_total=to_money(tips_total),
        tips_cash=to_money(aggregates["tips_cash"]),
        excluded_count=aggregates["excluded"],
    )


def expected_cash(opening_float: Decimal, summary: CashSummary) -> Decimal:
    """Lo que debería haber en el cajón: el fondo más lo cobrado en efectivo."""
    return to_money(Decimal(opening_float) + summary.cash.total)


class CashService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = CashRepository(db)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def current(self, tenant_id: str) -> Optional[CashSessionResponse]:
        """El turno abierto con sus cifras en vivo, o None si la caja está cerrada."""
        session = self.repo.get_open(tenant_id)
        return self._to_response(session) if session else None

    def list_sessions(self, tenant_id: str, page: int, page_size: int) -> CashSessionListResponse:
        items, total = self.repo.list_for_tenant(tenant_id, page, page_size)
        emails = self.repo.emails([u for s in items for u in (s.opened_by, s.closed_by)])
        return CashSessionListResponse(
            items=[self._to_response(s, emails) for s in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_session(self, tenant_id: str, session_id: str) -> CashSessionResponse:
        session = self.repo.get_by_id(tenant_id, session_id)
        if session is None:
            raise CashSessionNotFoundError(session_id)
        return self._to_response(session)

    # ------------------------------------------------------------------
    # Apertura y cierre
    # ------------------------------------------------------------------

    def open(self, current_user: CurrentUser, payload: OpenCashRequest) -> CashSessionResponse:
        if self.repo.get_open(current_user.tenant_id) is not None:
            raise CashSessionAlreadyOpenError(current_user.tenant_id)

        session = CashSession(
            id=generate_id("cs", 20),
            tenant_id=current_user.tenant_id,
            status="open",
            opened_by=current_user.user_id,
            opened_at=datetime.now(timezone.utc),
            opening_float=to_money(payload.opening_float),
        )
        try:
            self.repo.create(session)
            self.db.commit()
        except IntegrityError:
            # Otro cajero abrió al mismo tiempo: el índice único lo detuvo.
            self.db.rollback()
            raise CashSessionAlreadyOpenError(current_user.tenant_id)

        log_operation(
            tenant_id=current_user.tenant_id, user_id=current_user.user_id,
            action="OPEN_CASH", resource_id=session.id,
            after={"opening_float": str(session.opening_float)},
        )
        return self._to_response(session)

    def close(self, current_user: CurrentUser, payload: CloseCashRequest) -> CashSessionResponse:
        """
        Cierra el turno y congela el corte.

        Bloquea la fila del turno: si hay una venta cobrándose en este
        instante, el cierre espera a que termine y la cuenta; y una venta que
        llegue después ya no encuentra turno abierto.
        """
        session = self.repo.get_open(current_user.tenant_id, lock="update")
        if session is None:
            raise CashSessionNotFoundError("sin turno abierto")

        summary = build_summary(self.repo.sales_aggregates(session.id))
        expected = expected_cash(session.opening_float, summary)
        counted = to_money(payload.counted_cash)

        session.status = "closed"
        session.closed_by = current_user.user_id
        session.closed_at = datetime.now(timezone.utc)
        session.expected_cash = expected
        session.counted_cash = counted
        session.difference = to_money(counted - expected)
        session.summary = summary.model_dump(mode="json")
        session.notes = (payload.notes or "").strip() or None
        self.repo.save(session)
        self.db.commit()

        log_operation(
            tenant_id=current_user.tenant_id, user_id=current_user.user_id,
            action="CLOSE_CASH", resource_id=session.id,
            after={"expected": str(expected), "counted": str(counted), "difference": str(session.difference)},
        )
        logger.info(
            "Corte: tenant=%s session=%s esperado=%s contado=%s diferencia=%s",
            current_user.tenant_id, session.id, expected, counted, session.difference,
        )
        return self._to_response(session)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _to_response(self, session: CashSession, emails: Optional[dict[str, str]] = None) -> CashSessionResponse:
        if emails is None:
            emails = self.repo.emails([session.opened_by, session.closed_by])

        if session.status == "open":
            # Cifras en vivo: se recalculan en cada consulta.
            summary = build_summary(self.repo.sales_aggregates(session.id))
            expected = expected_cash(session.opening_float, summary)
        else:
            # Cerrado: lo que quedó congelado, aunque después cambie una venta.
            summary = CashSummary.model_validate(session.summary or {})
            expected = session.expected_cash

        return CashSessionResponse(
            session_id=session.id,
            status=session.status,
            opened_at=session.opened_at,
            opened_by=emails.get(session.opened_by),
            opening_float=session.opening_float,
            closed_at=session.closed_at,
            closed_by=emails.get(session.closed_by) if session.closed_by else None,
            expected_cash=expected,
            counted_cash=session.counted_cash,
            difference=session.difference,
            notes=session.notes,
            summary=summary,
        )
