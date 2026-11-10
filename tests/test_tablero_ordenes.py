"""
El tablero de órdenes: los pedidos que hacen los clientes desde la tienda.

Antes el panel solo tenía el reporte de ventas: un pedido en línea quedaba "preparando" para
siempre porque nadie tenía dónde moverlo. El tablero los agrupa por estado de entrega.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from orders.queue import QUEUE_STATES, queue_state
from orders.service import OrderService


# ═══════════════════════════════════════════════════════════════════
# En qué columna cae cada pedido
# ═══════════════════════════════════════════════════════════════════


class TestEstadoEnElTablero:
    @pytest.mark.parametrize("entrega", ["preparing", "ready", "in_transit", "completed"])
    def test_un_pedido_vivo_cae_en_su_estado_de_entrega(self, entrega):
        assert queue_state("paid", entrega) == entrega
        assert queue_state("pending", entrega) == entrega

    def test_lo_cancelado_gana_sobre_cualquier_otro_estado(self):
        assert queue_state("canceled", "preparing") == "canceled"
        assert queue_state("paid", "canceled") == "canceled"

    def test_fulfilled_historico_cuenta_como_entregado(self):
        """Valor viejo del pago que significaba "ya entregado", aunque la entrega no se haya movido."""
        assert queue_state("fulfilled", "preparing") == "completed"

    def test_cancelado_gana_incluso_a_fulfilled(self):
        assert queue_state("canceled", "completed") == "canceled"

    def test_los_estados_del_tablero_son_los_que_acepta_el_api(self):
        from typing import get_args

        from orders.models import UpdateSaleStatusRequest
        opcional = UpdateSaleStatusRequest.model_fields["fulfillment_status"].annotation
        aceptados = set(get_args(get_args(opcional)[0]))     # Optional[Literal[...]] → los valores
        assert set(QUEUE_STATES) == aceptados


# ═══════════════════════════════════════════════════════════════════
# Lo que devuelve el servicio
# ═══════════════════════════════════════════════════════════════════


def _pedido(oid, *, status="pending", entrega="preparing", modo="pickup", cliente="Ana", direccion=None, mesa=None):
    return SimpleNamespace(
        id=oid, status=status, fulfillment_type=modo, fulfillment_status=entrega,
        captured_by="self", payment_method=None,
        subtotal=Decimal("100.00"), shipping_cost=Decimal("0.00"), total=Decimal("100.00"),
        discount_amount=Decimal("0.00"), tip_amount=Decimal("0.00"),
        amount_received=None, change_given=None,
        table_number=mesa, customer_name=cliente, shipping_address=direccion,
        items=[SimpleNamespace(product_id="p1", product_name="Chilaquiles", quantity=2,
                               unit_price=Decimal("50.00"), subtotal=Decimal("100.00"))],
        created_at=datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc),
    )


def _servicio(pedidos):
    svc = OrderService.__new__(OrderService)
    svc.repo = SimpleNamespace(list_online_queue=lambda tenant_id, closed_since: [(p, None, None) for p in pedidos])
    return svc


class TestColaDeOrdenesEnLinea:
    def test_cuenta_cada_pedido_en_un_solo_estado(self):
        cola = _servicio([
            _pedido("o1", entrega="preparing"),
            _pedido("o2", entrega="preparing", status="paid"),
            _pedido("o3", entrega="ready", status="paid"),
            _pedido("o4", entrega="completed", status="paid"),
            _pedido("o5", status="canceled"),
        ]).online_queue("t_1")

        assert cola.counts == {"preparing": 2, "ready": 1, "in_transit": 0, "completed": 1, "canceled": 1}

    def test_avisa_cuantos_siguen_sin_cobrar(self):
        cola = _servicio([
            _pedido("o1", status="pending"),                       # activo y sin cobrar
            _pedido("o2", status="paid"),                          # activo y cobrado
            _pedido("o3", status="pending", entrega="completed"),  # entregado: ya no importa
            _pedido("o4", status="pending", entrega="canceled"),   # cancelado: tampoco
        ]).online_queue("t_1")

        assert cola.awaiting_payment == 1

    def test_el_pedido_a_domicilio_trae_la_direccion(self):
        direccion = {"street": "Calle 5", "city": "Monterrey", "phone": "8112345678"}
        cola = _servicio([_pedido("o1", modo="delivery", direccion=direccion)]).online_queue("t_1")
        assert cola.items[0].shipping_address == direccion

    def test_el_pedido_en_mesa_trae_el_numero_de_mesa(self):
        cola = _servicio([_pedido("o1", modo="dine_in", mesa="7")]).online_queue("t_1")
        assert cola.items[0].table_number == "7"

    def test_sin_pedidos_devuelve_todo_en_cero(self):
        cola = _servicio([]).online_queue("t_1")
        assert cola.items == [] and cola.awaiting_payment == 0
        assert set(cola.counts) == set(QUEUE_STATES) and sum(cola.counts.values()) == 0

    def test_pide_lo_cerrado_solo_de_las_ultimas_horas(self):
        visto = {}

        def lista(tenant_id, closed_since):
            visto["desde"] = closed_since
            return []

        svc = OrderService.__new__(OrderService)
        svc.repo = SimpleNamespace(list_online_queue=lista)
        svc.online_queue("t_1", closed_hours=24)

        antes = datetime.now(timezone.utc) - visto["desde"]
        assert 23.9 * 3600 < antes.total_seconds() < 24.1 * 3600
