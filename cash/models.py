"""
Esquemas del módulo de caja (turnos, apertura, cierre y corte).

Un turno se abre con el fondo con el que arranca el cajón y se cierra contando
el efectivo. La diferencia entre lo contado y lo esperado es lo que un dueño
quiere ver al final del día.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class OpenCashRequest(BaseModel):
    """Apertura: con cuánto efectivo arranca el cajón (puede ser cero)."""

    opening_float: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=10, decimal_places=2)


class CloseCashRequest(BaseModel):
    """Cierre: lo que realmente se contó en el cajón."""

    counted_cash: Decimal = Field(..., ge=0, max_digits=10, decimal_places=2)
    notes: Optional[str] = Field(default=None, max_length=500)


class MethodTotal(BaseModel):
    count: int = 0
    total: Decimal = Decimal("0.00")


class CashSummary(BaseModel):
    """
    Lo que pasó en el turno.

    Solo cuentan las ventas vigentes (pagadas o entregadas); las canceladas y
    reembolsadas se reportan aparte para que el dueño las vea sin que
    inflen el corte.
    """

    sales_count: int = 0
    gross: Decimal = Decimal("0.00")
    cash: MethodTotal = MethodTotal()
    card: MethodTotal = MethodTotal()
    transfer: MethodTotal = MethodTotal()
    other: MethodTotal = MethodTotal()
    discounts: Decimal = Decimal("0.00")
    tips_total: Decimal = Decimal("0.00")
    # Las propinas cobradas en efectivo van dentro del cajón.
    tips_cash: Decimal = Decimal("0.00")
    excluded_count: int = 0


class CashSessionResponse(BaseModel):
    session_id: str
    status: str
    opened_at: datetime
    opened_by: Optional[str] = None
    opening_float: Decimal
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    # Mientras el turno está abierto son cifras en vivo; al cerrar se congelan.
    expected_cash: Decimal
    counted_cash: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    notes: Optional[str] = None
    summary: CashSummary


class CashSessionListResponse(BaseModel):
    items: list[CashSessionResponse]
    total: int
    page: int
    page_size: int
