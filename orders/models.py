"""Pydantic schemas for the orders module."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Modos de entrega. `dine_in` es consumo en el local (pedido desde la mesa);
# `takeaway` se lo lleva ahora; `pickup` lo recoge después; `delivery` se envía.
FulfillmentType = Literal["dine_in", "takeaway", "pickup", "delivery"]
PaymentMethod = Literal["cash", "card", "transfer", "online"]


class ShippingAddress(BaseModel):
    """
    Dirección de entrega. Se guarda COPIADA dentro del pedido, no referenciada:
    si el cliente la edita después, el pedido histórico debe seguir diciendo a
    dónde se mandó realmente.
    """

    street: str = Field(..., min_length=3, max_length=200)
    neighborhood: Optional[str] = Field(default=None, max_length=120)
    city: str = Field(..., min_length=2, max_length=120)
    state: Optional[str] = Field(default=None, max_length=120)
    postal_code: Optional[str] = Field(default=None, max_length=12)
    phone: Optional[str] = Field(default=None, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=300)


class _FulfillmentFields(BaseModel):
    """Campos de entrega comunes al checkout del cliente y a la venta de mostrador."""

    fulfillment_type: FulfillmentType
    table_number: Optional[str] = Field(default=None, max_length=20)
    shipping_address: Optional[ShippingAddress] = None

    @model_validator(mode="after")
    def _coherent_fulfillment(self):
        """
        Cada modo exige sus propios datos. Validar aquí evita crear pedidos
        imposibles de entregar: un envío sin dirección, o una mesa sin número.
        """
        if self.fulfillment_type == "delivery" and self.shipping_address is None:
            raise ValueError("Un envío necesita dirección de entrega")
        if self.fulfillment_type == "dine_in" and not (self.table_number or "").strip():
            raise ValueError("Un consumo en el local necesita número de mesa")
        if self.fulfillment_type != "delivery" and self.shipping_address is not None:
            raise ValueError("Solo un envío lleva dirección de entrega")
        return self


class CreateOrderRequest(_FulfillmentFields):
    """Checkout del cliente (B2C): convierte su carrito de Redis en un pedido."""

    slug: str
    fulfillment_type: FulfillmentType = "pickup"
    on_insufficient_stock: Literal["reject", "skip"] = "reject"
    # "reject" (default): si algún producto/insumo no tiene stock → 409 con el
    #   detalle de lo faltante, no se crea nada.
    # "skip": crea el pedido omitiendo los productos no disponibles y los
    #   reporta en `skipped`.


class PosSaleItem(BaseModel):
    product_id: str = Field(..., max_length=60)
    quantity: int = Field(..., gt=0, le=10_000)


class DiscountInput(BaseModel):
    """
    Descuento sobre la venta completa: un porcentaje o un monto fijo.

    Se aplica al subtotal de productos (no al envío ni a la propina). Que no
    supere el subtotal lo valida el servicio, que es quien conoce el subtotal.
    """

    type: Literal["percent", "amount"]
    value: Decimal = Field(..., gt=0, max_digits=10, decimal_places=2)

    @model_validator(mode="after")
    def _percent_in_range(self):
        if self.type == "percent" and self.value > 100:
            raise ValueError("El porcentaje de descuento no puede pasar de 100")
        return self


class CreatePosSaleRequest(_FulfillmentFields):
    """
    Venta capturada por un empleado en el punto de venta.

    A diferencia del checkout B2C, los artículos llegan en el cuerpo: no hay
    carrito en Redis porque no hay cuenta de cliente detrás. El mostrador
    cobra en el momento, así que el pago nace saldado.
    """

    items: list[PosSaleItem] = Field(..., min_length=1, max_length=200)
    fulfillment_type: FulfillmentType = "takeaway"
    payment_method: PaymentMethod = "cash"
    customer_name: Optional[str] = Field(default=None, max_length=120)
    on_insufficient_stock: Literal["reject", "skip"] = "reject"

    # Caja. El servidor recalcula todo: lo que importa de estos tres campos es
    # lo que el cajero DECIDIÓ (cuánto descontar, cuánta propina, cuánto
    # entregó el cliente), nunca el total que muestre su pantalla.
    discount: Optional[DiscountInput] = None
    tip: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=10, decimal_places=2)
    amount_received: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)

    @model_validator(mode="after")
    def _received_only_in_cash(self):
        if self.amount_received is not None and self.payment_method != "cash":
            raise ValueError("Solo un pago en efectivo lleva monto recibido")
        return self


class OrderItemResponse(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class SkippedItemResponse(BaseModel):
    """A cart item that was left out of the order due to insufficient stock."""

    product_id: str
    product_name: str
    reason: str  # product_shortage | ingredient_shortage | product_and_ingredient | unavailable


class OrderResponse(BaseModel):
    order_id: str
    slug: str
    business_name: str
    status: str
    fulfillment_type: str
    fulfillment_status: str
    subtotal: Decimal
    shipping_cost: Decimal
    total: Decimal
    table_number: Optional[str] = None
    items: list[OrderItemResponse]
    skipped: list[SkippedItemResponse] = []
    created_at: datetime

    # Caja: lo que lleva el ticket. En pedidos en línea quedan en sus valores neutros.
    payment_method: Optional[str] = None
    discount_amount: Decimal = Decimal("0.00")
    tip_amount: Decimal = Decimal("0.00")
    amount_received: Optional[Decimal] = None
    change_given: Optional[Decimal] = None
    customer_name: Optional[str] = None
    # Lo que dice el ticket: quien cobró ("Eva P.") y desde qué caja ("Caja 1").
    cashier: Optional[str] = None
    register_name: Optional[str] = None


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    page: int
    page_size: int


# ── Vista del negocio (reporte de ventas) ──────────────────────────────


class SaleResponse(BaseModel):
    """
    Una venta vista desde el negocio.

    `customer_email` es opcional porque una venta de mostrador no tiene cuenta
    de cliente detrás; en ese caso `customer_name` cae en lo que el empleado
    haya capturado, o en una etiqueta genérica.
    """

    order_id: str
    status: str
    fulfillment_type: str
    fulfillment_status: str
    captured_by: str
    payment_method: Optional[str] = None
    subtotal: Decimal
    shipping_cost: Decimal
    discount_amount: Decimal = Decimal("0.00")
    tip_amount: Decimal = Decimal("0.00")
    amount_received: Optional[Decimal] = None
    change_given: Optional[Decimal] = None
    total: Decimal
    item_count: int
    customer_name: str
    customer_email: Optional[str] = None
    table_number: Optional[str] = None
    items: list[OrderItemResponse]
    created_at: datetime
    # Quién cobró, para uso interno: nombre completo y código de empleado, y la caja.
    cashier_name: Optional[str] = None
    cashier_code: Optional[str] = None
    register_name: Optional[str] = None


class SalesSummary(BaseModel):
    """
    Totales sobre el conjunto filtrado completo, no solo la página visible.

    `revenue` y `units` excluyen los pedidos cancelados; `canceled_count`
    los reporta aparte para que el negocio los vea sin que inflen el ingreso.
    """

    order_count: int
    canceled_count: int
    revenue: Decimal
    units: int
    # Cuánto del ingreso fue envío y no producto. Sin este desglose, el
    # reporte contaría la logística como venta de catálogo.
    shipping: Decimal = Decimal("0.00")
    # Lo mismo para las propinas (están dentro de `revenue` pero no son venta
    # de producto) y para lo que se descontó (ya restado de `revenue`).
    tips: Decimal = Decimal("0.00")
    discounts: Decimal = Decimal("0.00")


class UpdateSaleStatusRequest(BaseModel):
    """
    Cambio de estado hecho por el negocio.

    Los dos ejes se mueven por separado — se puede marcar un pedido como
    pagado sin tocar su preparación, o servirlo sin haberlo cobrado — y se
    permite cualquier destino, incluido volver atrás, porque el negocio
    necesita corregir un estado marcado por error. `Literal` ya impide que
    llegue un valor inventado (422 automático).

    `status` se mantiene con los valores históricos, incluido `fulfilled`,
    para no romper las ventas ya registradas ni el panel que las muestra.
    """

    status: Optional[Literal["pending", "paid", "refunded", "fulfilled", "canceled"]] = None
    fulfillment_status: Optional[
        Literal["preparing", "ready", "in_transit", "completed", "canceled"]
    ] = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.status is None and self.fulfillment_status is None:
            raise ValueError("Indica al menos un estado a cambiar")
        return self


class SalesReportResponse(BaseModel):
    # Encabezado del reporte descargable: el front lo necesita aunque el
    # filtro no devuelva ninguna venta, asi que no sale de los items.
    business_name: str

    items: list[SaleResponse]
    summary: SalesSummary
    total: int
    page: int
    page_size: int


# ── Dashboard: ventas reales de los últimos días ───────────────────────


class DayStat(BaseModel):
    """Lo vendido en un día del negocio (fecha local, no UTC)."""

    date: str
    revenue: Decimal
    orders: int


class TopProduct(BaseModel):
    product_id: str
    name: str
    units: int
    revenue: Decimal


class SalesStatsResponse(BaseModel):
    """
    Ventas COBRADAS (pagadas o entregadas) de los últimos `days` días, para el Dashboard.

    Los días sin ventas vienen con ceros: una gráfica que los omite miente.
    """

    days: list[DayStat]
    top_products: list[TopProduct]
    total_revenue: Decimal
    total_orders: int
    average_ticket: Optional[Decimal] = None
