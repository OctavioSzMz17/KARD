"""
Matemática del dinero de una venta de mostrador.

Funciones puras, sin base de datos: así se prueban con números y no con
mocks, y el servidor y la pantalla del punto de venta pueden calcular lo
mismo (la pantalla tiene su espejo en `pos-math.ts`).

Reglas, en el orden en que se aplican:

    1. El DESCUENTO resta del subtotal de productos. Nunca del envío ni de la
       propina, y no puede pasar del subtotal (no existen ventas con total
       negativo en productos).
    2. El ENVÍO se suma sin descuento.
    3. La PROPINA se suma al final; es del personal, no un ingreso del
       negocio, pero va en el cajón si se pagó en efectivo.
    4. El CAMBIO es lo recibido menos el total, y recibir de menos es un error.

Todos los importes se redondean a centavos hacia arriba en el medio (el
redondeo que espera quien cobra), no al par más cercano de `round()`.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from core.exceptions import InsufficientPaymentError, InvalidDiscountError

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

DiscountKind = Literal["percent", "amount"]


def to_money(value) -> Decimal:
    """Convierte a Decimal con exactamente dos decimales (mitad hacia arriba)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def discount_amount(subtotal: Decimal, kind: Optional[DiscountKind], value: Optional[Decimal]) -> Decimal:
    """
    Cuánto se descuenta de `subtotal`, en pesos.

    `percent` calcula sobre el subtotal; `amount` es un monto fijo. Ambos se
    topan con un error, no en silencio, si superan el subtotal: un descuento
    de $500 sobre una venta de $300 casi seguro es un error de captura, y
    recortarlo a $300 le ocultaría el error al cajero.
    """
    if kind is None or value is None:
        return ZERO

    if kind == "percent":
        if not (Decimal(0) < value <= Decimal(100)):
            raise InvalidDiscountError("El porcentaje de descuento debe estar entre 0 y 100")
        amount = to_money(subtotal * value / Decimal(100))
    else:
        amount = to_money(value)

    if amount > subtotal:
        raise InvalidDiscountError(
            f"El descuento (${amount}) no puede ser mayor que el subtotal (${subtotal})"
        )
    return amount


def order_total(subtotal: Decimal, discount: Decimal, shipping: Decimal, tip: Decimal) -> Decimal:
    """total = subtotal - descuento + envío + propina (la misma fórmula que verifica la base)."""
    return to_money(subtotal - discount + shipping + tip)


def change_for(total: Decimal, received: Decimal) -> Decimal:
    """Cambio a devolver. Recibir menos de lo cobrado es un error, no un cambio negativo."""
    received = to_money(received)
    if received < total:
        raise InsufficientPaymentError(f"Faltan ${to_money(total - received)} para cubrir el total")
    return to_money(received - total)
