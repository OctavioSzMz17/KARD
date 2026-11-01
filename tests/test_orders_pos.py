"""
Pruebas del punto de venta y los modos de entrega (migración 005).

Cubren las reglas que, si se rompen, producen errores silenciosos y caros:

  PS-04  El negocio solo acepta los modos de entrega que ofrece.
  PS-05  El envío se cobra según la configuración, y solo en envíos.
  PS-06  El costo de envío no se mezcla con el subtotal de productos.
  PS-07  Una venta de mostrador no necesita cuenta de cliente.

Igual que el resto de la suite, ejercitan la capa de servicio real con el
repositorio sustituido: no hace falta base de datos ni Docker.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.exceptions import InvalidMetadataError
from orders.models import CreateOrderRequest, CreatePosSaleRequest
from orders.service import WALK_IN_LABEL, OrderService


def tenant_con(**fulfillment):
    """Negocio con una configuración de entrega concreta."""
    base = {"dine_in": False, "takeaway": True, "pickup": True, "delivery": False}
    base.update(fulfillment)
    return SimpleNamespace(
        id="t_aaa",
        business_name="Sabor Norteño",
        slug="sabor-norteno",
        config={"fulfillment": base},
    )


# ═══════════════════════════════════════════════════════════════════
# PS-04 · El negocio solo acepta lo que ofrece
# ═══════════════════════════════════════════════════════════════════


class TestModosOfrecidos:
    def test_acepta_un_modo_habilitado(self):
        OrderService._assert_offered(tenant_con(takeaway=True), "takeaway")

    def test_rechaza_consumo_en_local_si_no_hay_local(self):
        """Una óptica no sirve platillos en mesa."""
        with pytest.raises(InvalidMetadataError):
            OrderService._assert_offered(tenant_con(dine_in=False), "dine_in")

    def test_rechaza_envio_si_el_negocio_no_reparte(self):
        with pytest.raises(InvalidMetadataError):
            OrderService._assert_offered(tenant_con(delivery=False), "delivery")

    def test_un_negocio_sin_configurar_no_ofrece_envio(self):
        """
        Los valores por defecto son conservadores a propósito: nadie debería
        estar aceptando envíos sin haberlo decidido.
        """
        sin_config = SimpleNamespace(id="t_x", business_name="X", slug="x", config=None)
        with pytest.raises(InvalidMetadataError):
            OrderService._assert_offered(sin_config, "delivery")
        OrderService._assert_offered(sin_config, "pickup")  # este sí, es universal


# ═══════════════════════════════════════════════════════════════════
# PS-05 y PS-06 · Cálculo del envío
# ═══════════════════════════════════════════════════════════════════


class TestCostoDeEnvio:
    def test_solo_los_envios_cobran(self):
        """Comer en el local, llevárselo o recogerlo no tienen costo logístico."""
        t = tenant_con(delivery=True, dine_in=True, shipping_flat_fee="35.00")
        for modo in ("dine_in", "takeaway", "pickup"):
            assert OrderService._shipping_cost_for(t, modo, Decimal("100.00")) == Decimal("0.00")

    def test_el_envio_cobra_la_tarifa_configurada(self):
        t = tenant_con(delivery=True, shipping_flat_fee="35.00")
        assert OrderService._shipping_cost_for(t, "delivery", Decimal("100.00")) == Decimal("35.00")

    def test_envio_gratis_al_alcanzar_el_minimo(self):
        t = tenant_con(delivery=True, shipping_flat_fee="35.00", free_shipping_above="400.00")
        assert OrderService._shipping_cost_for(t, "delivery", Decimal("399.99")) == Decimal("35.00")
        assert OrderService._shipping_cost_for(t, "delivery", Decimal("400.00")) == Decimal("0.00")
        assert OrderService._shipping_cost_for(t, "delivery", Decimal("500.00")) == Decimal("0.00")

    def test_sin_tarifa_configurada_el_envio_es_gratis(self):
        t = tenant_con(delivery=True)
        assert OrderService._shipping_cost_for(t, "delivery", Decimal("50.00")) == Decimal("0.00")


# ═══════════════════════════════════════════════════════════════════
# Coherencia de la petición
# ═══════════════════════════════════════════════════════════════════


class TestDatosSegunElModo:
    """Cada modo exige sus datos: sin esto se crean pedidos imposibles de entregar."""

    def test_envio_exige_direccion(self):
        with pytest.raises(ValidationError):
            CreatePosSaleRequest(
                items=[{"product_id": "p_1", "quantity": 1}], fulfillment_type="delivery"
            )

    def test_mesa_exige_numero(self):
        with pytest.raises(ValidationError):
            CreatePosSaleRequest(
                items=[{"product_id": "p_1", "quantity": 1}], fulfillment_type="dine_in"
            )

    def test_mesa_en_blanco_no_cuenta(self):
        with pytest.raises(ValidationError):
            CreatePosSaleRequest(
                items=[{"product_id": "p_1", "quantity": 1}],
                fulfillment_type="dine_in",
                table_number="   ",
            )

    def test_lo_que_no_es_envio_no_lleva_direccion(self):
        """Una dirección en un pedido para llevar es un dato contradictorio."""
        with pytest.raises(ValidationError):
            CreateOrderRequest(
                slug="x",
                fulfillment_type="takeaway",
                shipping_address={"street": "Calle 1", "city": "Monterrey"},
            )

    def test_peticion_valida_de_mesa(self):
        req = CreatePosSaleRequest(
            items=[{"product_id": "p_1", "quantity": 2}],
            fulfillment_type="dine_in",
            table_number="7",
        )
        assert req.table_number == "7"
        assert req.payment_method == "cash"  # el mostrador cobra en efectivo por defecto

    def test_el_mostrador_rechaza_una_venta_sin_articulos(self):
        with pytest.raises(ValidationError):
            CreatePosSaleRequest(items=[], fulfillment_type="takeaway")

    def test_cantidades_deben_ser_positivas(self):
        with pytest.raises(ValidationError):
            CreatePosSaleRequest(
                items=[{"product_id": "p_1", "quantity": 0}], fulfillment_type="takeaway"
            )


# ═══════════════════════════════════════════════════════════════════
# PS-07 · Venta sin cuenta de cliente
# ═══════════════════════════════════════════════════════════════════


class TestVentaSinCliente:
    """El reporte del negocio debe seguir leyéndose cuando no hay consumer."""

    @staticmethod
    def _orden(customer_name=None):
        return SimpleNamespace(
            id="ord_1", status="paid", fulfillment_type="takeaway",
            fulfillment_status="preparing", captured_by="staff", payment_method="cash",
            subtotal=Decimal("40.00"), shipping_cost=Decimal("0.00"), total=Decimal("40.00"),
            discount_amount=Decimal("0.00"), tip_amount=Decimal("0.00"),
            amount_received=None, change_given=None,
            table_number=None, customer_name=customer_name, items=[],
            created_at=__import__("datetime").datetime(2026, 10, 29),
        )

    def test_usa_el_nombre_capturado_por_el_empleado(self):
        r = OrderService._sale_to_response(self._orden("Doña Mari"), None)
        assert r.customer_name == "Doña Mari"
        assert r.customer_email is None

    def test_sin_nombre_usa_una_etiqueta_honesta(self):
        """Mejor una etiqueta explícita que un campo vacío en el reporte."""
        r = OrderService._sale_to_response(self._orden(None), None)
        assert r.customer_name == WALK_IN_LABEL

    def test_con_cliente_registrado_prefiere_su_cuenta(self):
        consumer = SimpleNamespace(name="Octavio", email="cliente@kard.dev")
        r = OrderService._sale_to_response(self._orden("ignorado"), consumer)
        assert r.customer_name == "Octavio"
        assert r.customer_email == "cliente@kard.dev"
