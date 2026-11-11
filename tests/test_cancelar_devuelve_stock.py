"""
Cancelar un pedido deja en una cola lo que hay que devolver al inventario.

Cancelar NO toca el inventario en la petición: marca el pedido y deja UN trabajo (`StockReturn`)
con las líneas a devolver, en la misma transacción. Un proceso aparte lo aplica
(`tests/test_cola_devoluciones.py`). Así cancelar no espera a otros pedidos que comparten insumos.

Reglas que se prueban aquí:
  · cancelar un pedido en marcha deja un trabajo con los insumos de la receta (y el producto, si
    lleva stock), y no mueve el inventario;
  · deja UN solo trabajo, aunque se cancele de nuevo o por el otro eje (pago / entrega);
  · un pedido cancelado no se reabre (devolver y volver a descontar podría no alcanzar);
  · mover un pedido sin cancelarlo no deja ningún trabajo.
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.exceptions import OrderCanceledError
from db.models import Product, ProductRecipe, Tenant
from orders.queue import is_canceled
from orders.service import OrderService


# ═══════════════════════════════════════════════════════════════════
# Una base falsa que sabe qué producto se le pide
# ═══════════════════════════════════════════════════════════════════


def _valor(condicion):
    """El valor con el que se compara en `Columna == valor`."""
    return getattr(getattr(condicion, "right", None), "value", None)


class _Consulta:
    def __init__(self, db, entidad):
        self.db, self.entidad, self.clave = db, entidad, None

    def filter(self, *condiciones):
        self.clave = _valor(condiciones[0]) if condiciones else None
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self.db.productos.get(self.clave)

    def all(self):
        return list(self.db.recetas.get(self.clave, []))

    def scalar(self):
        return self.db.business_type


class FakeDb:
    def __init__(self, *, business_type, productos=(), recetas=None):
        self.business_type = business_type
        self.productos = {p.id: p for p in productos}
        self.recetas = recetas or {}      # id del platillo → [recetas]
        self.agregados = []
        self.commits = 0

    def query(self, entidad):
        if entidad is Tenant.business_type:
            return _Consulta(self, entidad)
        assert entidad in (Product, ProductRecipe), entidad
        return _Consulta(self, entidad)

    def add(self, objeto):
        self.agregados.append(objeto)

    def commit(self):
        self.commits += 1

    def refresh(self, objeto):
        pass

    @property
    def trabajos(self):
        return [o for o in self.agregados if o.__class__.__name__ == "StockReturn"]

    @property
    def devolver(self):
        """Lo que la cola devolverá: id del producto → cantidad, sumando todos los trabajos."""
        total = {}
        for trabajo in self.trabajos:
            for linea in trabajo.lines:
                total[linea["product_id"]] = total.get(linea["product_id"], 0) + linea["quantity"]
        return total


def producto(pid, *, tipo, stock, nombre="Producto"):
    return SimpleNamespace(id=pid, name=nombre, product_type=tipo, inventory_count=stock, tenant_id="t_1")


def receta(insumo_id, cantidad):
    return SimpleNamespace(insumo_product_id=insumo_id, quantity=cantidad)


def pedido(*partidas, status="pending", entrega="preparing"):
    return SimpleNamespace(
        id="ord_1", tenant_id="t_1", status=status, fulfillment_type="pickup", fulfillment_status=entrega,
        captured_by="self", payment_method=None, consumer_id=None, user_id=None,
        subtotal=Decimal("100.00"), shipping_cost=Decimal("0.00"), total=Decimal("100.00"),
        discount_amount=Decimal("0.00"), tip_amount=Decimal("0.00"), amount_received=None, change_given=None,
        table_number=None, customer_name="Ana", shipping_address=None,
        items=[
            SimpleNamespace(product_id=pid, product_name=pid, quantity=cant,
                            unit_price=Decimal("50.00"), subtotal=Decimal("50.00") * cant)
            for pid, cant in partidas
        ],
        created_at=datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc),
    )


def servicio(db, orden):
    svc = OrderService.__new__(OrderService)
    svc.db = db
    svc.repo = SimpleNamespace(
        get_by_id_for_tenant=lambda order_id, tenant_id, lock=False: orden if order_id == orden.id else None,
    )
    return svc


# ═══════════════════════════════════════════════════════════════════
# Cancelar
# ═══════════════════════════════════════════════════════════════════


class TestCancelarDejaLaDevolucionEnLaCola:
    def test_un_platillo_deja_los_insumos_de_su_receta_y_no_toca_el_inventario(self):
        chilaquiles = producto("p_chil", tipo="final", stock=0, nombre="Chilaquiles")
        maiz = producto("i_maiz", tipo="insumo", stock=4)
        pollo = producto("i_pollo", tipo="insumo", stock=10)
        db = FakeDb(
            business_type="restaurant", productos=[chilaquiles, maiz, pollo],
            recetas={"p_chil": [receta("i_maiz", 2), receta("i_pollo", 1)]},
        )
        orden = pedido(("p_chil", 3))

        servicio(db, orden).update_sale_status("t_1", "ord_1", "canceled", "canceled", user_id="u_9")

        assert db.devolver == {"i_maiz": 2 * 3, "i_pollo": 1 * 3}    # un platillo no lleva existencias
        # Cancelar no mueve el inventario: lo hace la cola, fuera de la petición.
        assert (maiz.inventory_count, pollo.inventory_count, chilaquiles.inventory_count) == (4, 10, 0)
        assert db.commits == 1                                       # pedido y trabajo, una transacción

    def test_el_trabajo_queda_a_nombre_del_pedido_y_de_quien_cancela(self):
        db = FakeDb(
            business_type="restaurant",
            productos=[producto("p_chil", tipo="final", stock=0, nombre="Chilaquiles"), producto("i_maiz", tipo="insumo", stock=4)],
            recetas={"p_chil": [receta("i_maiz", 2)]},
        )
        servicio(db, pedido(("p_chil", 3))).update_sale_status("t_1", "ord_1", "canceled", user_id="u_9")

        [trabajo] = db.trabajos
        assert (trabajo.order_id, trabajo.tenant_id, trabajo.user_id, trabajo.status) == ("ord_1", "t_1", "u_9", "pending")
        [linea] = trabajo.lines
        assert (linea["product_id"], linea["quantity"]) == ("i_maiz", 6)
        assert "ord_1" in linea["note"] and "Chilaquiles" in linea["note"]

    def test_varias_partidas_dejan_cada_una_lo_suyo(self):
        db = FakeDb(
            business_type="restaurant",
            productos=[
                producto("p_chil", tipo="final", stock=0), producto("p_sopa", tipo="final", stock=0),
                producto("i_maiz", tipo="insumo", stock=0), producto("i_queso", tipo="insumo", stock=1),
            ],
            recetas={"p_chil": [receta("i_maiz", 2)], "p_sopa": [receta("i_maiz", 1), receta("i_queso", 3)]},
        )
        servicio(db, pedido(("p_chil", 2), ("p_sopa", 2))).update_sale_status("t_1", "ord_1", "canceled")

        assert db.devolver == {"i_maiz": 2 * 2 + 1 * 2, "i_queso": 3 * 2}
        assert len(db.trabajos) == 1                                 # un pedido, un trabajo

    def test_un_platillo_sin_receta_no_deja_trabajo_pero_igual_se_cancela(self):
        db = FakeDb(business_type="restaurant", productos=[producto("p_esq", tipo="final", stock=0)])
        orden = pedido(("p_esq", 2))
        servicio(db, orden).update_sale_status("t_1", "ord_1", "canceled")
        assert db.trabajos == [] and db.commits == 1
        assert orden.status == "canceled"

    def test_en_una_optica_se_devuelve_el_stock_del_propio_producto(self):
        lente = producto("p_len", tipo="final", stock=3, nombre="Lente")
        db = FakeDb(business_type="optician", productos=[lente])
        servicio(db, pedido(("p_len", 2))).update_sale_status("t_1", "ord_1", "canceled")

        assert db.devolver == {"p_len": 2}
        assert lente.inventory_count == 3

    def test_cancelar_solo_la_entrega_tambien_deja_la_devolucion(self):
        db = FakeDb(
            business_type="restaurant", productos=[producto("p_chil", tipo="final", stock=0), producto("i_maiz", tipo="insumo", stock=0)],
            recetas={"p_chil": [receta("i_maiz", 2)]},
        )
        servicio(db, pedido(("p_chil", 1))).update_sale_status("t_1", "ord_1", None, "canceled")
        assert db.devolver == {"i_maiz": 2}

    def test_un_producto_dado_de_baja_despues_tambien_recupera_lo_suyo(self):
        # El servicio no filtra por is_deleted: el insumo sigue ahí aunque ya no se venda.
        maiz = producto("i_maiz", tipo="insumo", stock=0)
        maiz.is_deleted = True
        db = FakeDb(
            business_type="restaurant", productos=[producto("p_chil", tipo="final", stock=0), maiz],
            recetas={"p_chil": [receta("i_maiz", 2)]},
        )
        servicio(db, pedido(("p_chil", 1))).update_sale_status("t_1", "ord_1", "canceled")
        assert db.devolver == {"i_maiz": 2}

    def test_un_producto_que_ya_no_existe_no_rompe_la_cancelacion(self):
        db = FakeDb(business_type="restaurant", productos=[])
        r = servicio(db, pedido(("p_fantasma", 1))).update_sale_status("t_1", "ord_1", "canceled")
        assert r.status == "canceled" and db.commits == 1 and db.trabajos == []


class TestSoloUnaVez:
    def _cancelado(self, **estado):
        db = FakeDb(
            business_type="restaurant",
            productos=[producto("p_chil", tipo="final", stock=0), producto("i_maiz", tipo="insumo", stock=10)],
            recetas={"p_chil": [receta("i_maiz", 2)]},
        )
        return db, pedido(("p_chil", 1), **estado)

    def test_cancelar_dos_veces_deja_un_solo_trabajo(self):
        db, orden = self._cancelado()
        svc = servicio(db, orden)
        svc.update_sale_status("t_1", "ord_1", "canceled", "canceled")
        svc.update_sale_status("t_1", "ord_1", "canceled", "canceled")
        assert len(db.trabajos) == 1 and db.devolver == {"i_maiz": 2}      # 2, no 4

    def test_cancelar_primero_el_pago_y_luego_la_entrega_deja_un_solo_trabajo(self):
        db, orden = self._cancelado()
        svc = servicio(db, orden)
        svc.update_sale_status("t_1", "ord_1", "canceled", None)
        svc.update_sale_status("t_1", "ord_1", None, "canceled")
        assert len(db.trabajos) == 1

    def test_un_pedido_cancelado_antes_de_esta_regla_no_deja_nada_ahora(self):
        """Ya venía cancelado: no hay transición, así que no se inventa una devolución."""
        db, orden = self._cancelado(status="canceled", entrega="canceled")
        servicio(db, orden).update_sale_status("t_1", "ord_1", "canceled", "canceled")
        assert db.trabajos == []


class TestUnCanceladoNoSeReabre:
    @pytest.mark.parametrize("cambio", [
        {"new_status": "paid"},
        {"new_status": "pending"},
        {"new_fulfillment_status": "preparing"},
        {"new_status": "paid", "new_fulfillment_status": "ready"},
    ])
    def test_reabrirlo_se_rechaza_y_no_cambia_nada(self, cambio):
        maiz = producto("i_maiz", tipo="insumo", stock=10)
        db = FakeDb(
            business_type="restaurant", productos=[producto("p_chil", tipo="final", stock=0), maiz],
            recetas={"p_chil": [receta("i_maiz", 2)]},
        )
        orden = pedido(("p_chil", 1), status="canceled", entrega="canceled")

        with pytest.raises(OrderCanceledError):
            servicio(db, orden).update_sale_status("t_1", "ord_1", **cambio)

        assert (orden.status, orden.fulfillment_status) == ("canceled", "canceled")
        assert maiz.inventory_count == 10 and db.commits == 0 and db.trabajos == []

    def test_con_los_dos_ejes_cancelados_tampoco_se_cambia_solo_uno(self):
        """Marcar "pagado" dejaría la entrega cancelada: el tablero lo vería cancelado y el reporte, vendido."""
        db = FakeDb(business_type="restaurant", productos=[producto("p_chil", tipo="final", stock=0)])
        orden = pedido(("p_chil", 1), status="canceled", entrega="canceled")
        with pytest.raises(OrderCanceledError):
            servicio(db, orden).update_sale_status("t_1", "ord_1", "paid", None)
        with pytest.raises(OrderCanceledError):
            servicio(db, orden).update_sale_status("t_1", "ord_1", None, "completed")

    def test_cancelado_solo_por_el_pago_se_puede_mover_la_entrega_sin_reabrir(self):
        """Así se cancelaban desde Ventas antes: el pago cancelado y la entrega todavía "preparando"."""
        db = FakeDb(business_type="restaurant", productos=[producto("p_chil", tipo="final", stock=0)])
        orden = pedido(("p_chil", 1), status="canceled", entrega="preparing")
        r = servicio(db, orden).update_sale_status("t_1", "ord_1", None, "completed")
        assert r.status == "canceled" and r.fulfillment_status == "completed"

    def test_repetir_la_cancelacion_no_es_un_error(self):
        db = FakeDb(business_type="restaurant", productos=[producto("p_chil", tipo="final", stock=0)])
        orden = pedido(("p_chil", 1), status="canceled", entrega="canceled")
        r = servicio(db, orden).update_sale_status("t_1", "ord_1", "canceled", "canceled")
        assert r.status == "canceled"

    def test_el_error_se_contesta_con_un_409_claro(self):
        from main import order_canceled_exception_handler

        respuesta = asyncio.run(order_canceled_exception_handler(None, OrderCanceledError("ord_1")))
        cuerpo = json.loads(respuesta.body)
        assert respuesta.status_code == 409
        assert cuerpo["code"] == "ORDER_CANCELED"
        assert "no se puede reabrir" in cuerpo["error"]


class TestMoverSinCancelarNoDejaTrabajo:
    @pytest.mark.parametrize("cambio", [
        {"new_fulfillment_status": "ready"},
        {"new_fulfillment_status": "completed"},
        {"new_status": "paid"},
    ])
    def test_los_demas_cambios_no_dejan_devolucion(self, cambio):
        maiz = producto("i_maiz", tipo="insumo", stock=10)
        db = FakeDb(
            business_type="restaurant", productos=[producto("p_chil", tipo="final", stock=0), maiz],
            recetas={"p_chil": [receta("i_maiz", 2)]},
        )
        servicio(db, pedido(("p_chil", 1))).update_sale_status("t_1", "ord_1", **cambio)

        assert maiz.inventory_count == 10 and db.trabajos == []
        assert db.commits == 1

    def test_un_pedido_de_otro_negocio_da_404(self):
        from core.exceptions import OrderNotFoundError
        db = FakeDb(business_type="restaurant")
        with pytest.raises(OrderNotFoundError):
            servicio(db, pedido()).update_sale_status("t_1", "ord_ajena", "canceled")


class TestRegla:
    def test_cancelado_es_cancelado_por_pago_o_por_entrega(self):
        assert is_canceled("canceled", "preparing") and is_canceled("paid", "canceled")
        assert not is_canceled("paid", "completed") and not is_canceled("pending", "preparing")
