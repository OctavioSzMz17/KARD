"""
Cuentas del Dashboard sin base de datos: qué días cubre la gráfica y cómo se rellenan.

El navegador manda su desfase con UTC (`Date.getTimezoneOffset()`: minutos que hay que
RESTAR a la hora UTC para tener la hora local; en Ciudad de México es 360). Así "hoy" y cada día
de la gráfica son los del negocio, no los de Greenwich: una venta de las 9 de la noche no cae en el
día siguiente.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from orders.money import ZERO, to_money

MAX_DAYS = 31


def local_today(now_utc: datetime, tz_offset_minutes: int) -> date:
    """La fecha de hoy en el lugar del negocio."""
    return (now_utc - timedelta(minutes=tz_offset_minutes)).date()


def window_start_utc(today: date, days: int, tz_offset_minutes: int) -> datetime:
    """El instante UTC en que empieza el primer día de la ventana (medianoche local de ese día)."""
    first = today - timedelta(days=days - 1)
    return datetime(first.year, first.month, first.day, tzinfo=timezone.utc) + timedelta(minutes=tz_offset_minutes)


def fill_days(rows: dict[date, tuple[Decimal, int]], today: date, days: int) -> list[dict]:
    """
    Un renglón por día, del más viejo al de hoy, con ceros donde no hubo ventas.

    Una gráfica que se salta los días sin ventas miente: un día vacío es un dato.
    """
    out = []
    for back in range(days - 1, -1, -1):
        day = today - timedelta(days=back)
        revenue, orders = rows.get(day, (ZERO, 0))
        out.append({"date": day.isoformat(), "revenue": to_money(revenue), "orders": int(orders)})
    return out


def average_ticket(revenue: Decimal, orders: int) -> Optional[Decimal]:
    return to_money(revenue / orders) if orders else None
