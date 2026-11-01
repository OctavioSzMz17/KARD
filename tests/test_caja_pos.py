"""
Pruebas del punto de venta como CAJA: descuento, propina, cambio, turnos y
códigos de producto.

  PS-18  La matemática del dinero (descuento, total y cambio) es exacta.
  PS-19  La petición de venta rechaza lo que no tiene sentido.
  PS-20  Cobrar exige caja abierta, y valida el dinero ANTES de mover inventario.
  PS-21  La venta guarda su desglose y el total cuadra con la fórmula de la base.
  PS-22  El corte: efectivo esperado, diferencia y turno único.
  PS-23  SKU y código de barras: únicos por negocio, y editables.

Igual que el resto de la suite: servicio real con repositorio y sesión de
mentira; no necesitan base de datos ni Docker.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from cash.models import CloseCashRequest, OpenCashRequest
from cash.service import CashService, build_summary, expected_cash
from core.exceptions import (
    CashClosedError,
    CashSessionAlreadyOpenError,
    CashSessionNotFoundError,
    DuplicateProductCodeError,
    InsufficientPaymentError,
    InvalidDiscountError,
)
from orders.models import CreatePosSaleRequest
from orders.money import change_for, discount_amount, order_total, to_money
from orders.service import OrderService
from products.models import ProductCreate, ProductUpdate
from tests.conftest import FakeSession, apply_orm_defaults, make_product

D = Decimal


# ═══════════════════════════════════════════════════════════════════
# PS-18 · La matemática del dinero
# ═══════════════════════════════════════════════════════════════════


class TestDescuento:
    def test_porcentaje_sobre_el_subtotal(self):
        assert discount_amount(D("200.00"), "percent", D("10")) == D("20.00")

    def test_monto_fijo(self):
        assert discount_amount(D("200.00"), "amount", D("35.50")) == D("35.50")

    def test_sin_descuento_es_cero(self):
        assert discount_amount(D("200.00"), None, None) == D("0.00")

    def test_redondea_la_mitad_hacia_arriba_como_se_cobra(self):
        """15 % de 10.10 son 1.515: se cobra 1.52, no el 1.52/1.51 caprichoso del redondeo bancario."""
        assert discount_amount(D("10.10"), "percent", D("15")) == D("1.52")
        # y 12.5 % de 0.04 = 0.005 → 0.01 (el redondeo bancario daría 0.00)
        assert discount_amount(D("0.04"), "percent", D("12.5")) == D("0.01")

    def test_cien_por_ciento_es_valido(self):
        assert discount_amount(D("80.00"), "percent", D("100")) == D("80.00")

    def test_un_monto_mayor_al_subtotal_es_un_error_no_se_recorta(self):
        """$500 sobre una venta de $300 es casi seguro un error de captura: se avisa, no se oculta."""
        with pytest.raises(InvalidDiscountError):
            discount_amount(D("300.00"), "amount", D("500"))

    def test_porcentaje_fuera_de_rango(self):
        for malo in (D("0"), D("-5"), D("100.01")):
            with pytest.raises(InvalidDiscountError):
                discount_amount(D("100.00"), "percent", malo)


class TestTotalYCambio:
    def test_la_formula_del_total(self):
        """total = subtotal - descuento + envío + propina (la misma que verifica la base)."""
        assert order_total(D("200.00"), D("20.00"), D("35.00"), D("15.00")) == D("230.00")

    def test_el_descuento_no_toca_el_envio_ni_la_propina(self):
        sin = order_total(D("100.00"), D("0.00"), D("30.00"), D("10.00"))
        con = order_total(D("100.00"), D("40.00"), D("30.00"), D("10.00"))
        assert sin - con == D("40.00")

    def test_cambio_exacto(self):
        assert change_for(D("230.00"), D("230.00")) == D("0.00")

    def test_cambio_de_un_billete(self):
        assert change_for(D("87.50"), D("200")) == D("112.50")

    def test_recibir_de_menos_es_un_error_con_lo_que_falta(self):
        with pytest.raises(InsufficientPaymentError) as e:
            change_for(D("100.00"), D("80"))
        assert "20.00" in str(e.value)

    def test_to_money_siempre_dos_decimales(self):
        assert str(to_money("3")) == "3.00"
        assert str(to_money(D("2.675"))) == "2.68"


# ═══════════════════════════════════════════════════════════════════
# PS-19 · La petición de venta
# ═══════════════════════════════════════════════════════════════════


def venta(**extra):
    base = {"items": [{"product_id": "p_1", "quantity": 1}]}
    base.update(extra)
    return CreatePosSaleRequest(**base)


class TestPeticionDeVenta:
    def test_una_venta_simple_no_necesita_nada_de_caja(self):
        r = venta()
        assert r.discount is None and r.tip == D("0.00") and r.amount_received is None

    def test_efectivo_con_monto_recibido(self):
        r = venta(payment_method="cash", amount_received="200")
        assert r.amount_received == D("200")

    def test_monto_recibido_solo_en_efectivo(self):
        """Con tarjeta no hay cambio que dar: mandarlo es un error del cliente."""
        with pytest.raises(ValidationError):
            venta(payment_method="card", amount_received="200")

    def test_propina_negativa_se_rechaza(self):
        with pytest.raises(ValidationError):
            venta(tip="-5")

    def test_descuento_en_cero_o_negativo_se_rechaza(self):
        for v in ("0", "-10"):
            with pytest.raises(ValidationError):
                venta(discount={"type": "amount", "value": v})

    def test_porcentaje_mayor_a_cien_se_rechaza(self):
        with pytest.raises(ValidationError):
            venta(discount={"type": "percent", "value": "120"})

    def test_tipo_de_descuento_inventado_se_rechaza(self):
        with pytest.raises(ValidationError):
            venta(discount={"type": "regalo", "value": "10"})

    def test_un_importe_con_tres_decimales_se_rechaza(self):
        with pytest.raises(ValidationError):
            venta(tip="1.505")


# ═══════════════════════════════════════════════════════════════════
# PS-20 / PS-21 · Cobrar: caja abierta y desglose guardado
# ═══════════════════════════════════════════════════════════════════


class FakeDb:
    """Sesión mínima: guarda lo que se agrega y cuenta commits/rollbacks."""

    def __init__(self, tenant):
        self.tenant = tenant
        self.added, self.commits, self.rollbacks = [], 0, 0

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.tenant

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            apply_orm_defaults(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def negocio():
    return SimpleNamespace(
        id="t_aaa", business_name="Sabor", slug="sabor",
        config={"fulfillment": {"dine_in": True, "takeaway": True, "pickup": True, "delivery": False}},
    )


def producto(precio="100.00", stock=10):
    return SimpleNamespace(id="p_1", name="Taco", price=D(precio), inventory_count=stock)


def servicio_de_venta(monkeypatch, *, caja_abierta=True, stock=10, precio="100.00"):
    """OrderService con la caja y el análisis de stock sustituidos."""
    db = FakeDb(negocio())
    svc = OrderService.__new__(OrderService)
    svc.db = db
    prod = producto(precio, stock)
    svc._analyze_cart = lambda tenant_id, cart: (
        [{"product": prod, "quantity": cart["p_1"]["quantity"], "ingredients": []}], []
    )

    class CajaFalsa:
        def __init__(self, _db):
            pass

        def get_open(self, tenant_id, lock=None):
            return SimpleNamespace(id="cs_1") if caja_abierta else None

    monkeypatch.setattr("orders.service.CashRepository", CajaFalsa)
    return svc, db, prod


class TestCobrarExigeCaja:
    def test_sin_caja_abierta_no_se_cobra(self, monkeypatch):
        svc, db, prod = servicio_de_venta(monkeypatch, caja_abierta=False)

        with pytest.raises(CashClosedError):
            svc.create_pos_sale("t_aaa", "u_1", venta(items=[{"product_id": "p_1", "quantity": 2}]))

        assert prod.inventory_count == 10     # no se movió el inventario
        assert db.added == []

    def test_un_descuento_invalido_no_descuenta_inventario(self, monkeypatch):
        """Se valida el dinero antes de tocar existencias: un error de captura no debe costar stock."""
        svc, db, prod = servicio_de_venta(monkeypatch)
        peticion = venta(items=[{"product_id": "p_1", "quantity": 2}],
                         discount={"type": "amount", "value": "500"})   # subtotal = 200

        with pytest.raises(InvalidDiscountError):
            svc.create_pos_sale("t_aaa", "u_1", peticion)

        assert prod.inventory_count == 10
        assert db.rollbacks == 1

    def test_efectivo_insuficiente_no_descuenta_inventario(self, monkeypatch):
        svc, db, prod = servicio_de_venta(monkeypatch)
        peticion = venta(items=[{"product_id": "p_1", "quantity": 2}], amount_received="150")  # total 200

        with pytest.raises(InsufficientPaymentError):
            svc.create_pos_sale("t_aaa", "u_1", peticion)

        assert prod.inventory_count == 10


class TestLaVentaGuardaSuDesglose:
    def _vender(self, monkeypatch, **extra):
        svc, db, prod = servicio_de_venta(monkeypatch)
        r = svc.create_pos_sale(
            "t_aaa", "u_1",
            venta(items=[{"product_id": "p_1", "quantity": 2}], **extra),
            cashier="ana",
        )
        orden = next(o for o in db.added if o.__class__.__name__ == "Order")
        return r, orden, prod, db

    def test_total_con_descuento_y_propina(self, monkeypatch):
        r, orden, _, _ = self._vender(
            monkeypatch,
            discount={"type": "percent", "value": "10"}, tip="15", payment_method="card",
        )

        # subtotal 200 - 20 de descuento + 15 de propina
        assert (orden.subtotal, orden.discount_amount, orden.tip_amount, orden.total) == (
            D("200.00"), D("20.00"), D("15.00"), D("195.00"))
        assert r.total == D("195.00") and r.discount_amount == D("20.00") and r.tip_amount == D("15.00")

    def test_la_formula_que_exige_la_base_se_cumple(self, monkeypatch):
        """Es la misma CHECK de la migración 007: si el código la rompiera, la base rechazaría la venta."""
        _, o, _, _ = self._vender(monkeypatch, discount={"type": "amount", "value": "33.33"}, tip="7.77")
        assert o.total == o.subtotal - o.discount_amount + o.shipping_cost + o.tip_amount

    def test_efectivo_guarda_lo_recibido_y_el_cambio(self, monkeypatch):
        r, o, _, _ = self._vender(monkeypatch, payment_method="cash", amount_received="500")

        assert (o.amount_received, o.change_given) == (D("500.00"), D("300.00"))
        assert o.change_given == o.amount_received - o.total   # la CHECK de la base
        assert r.change_given == D("300.00")

    def test_tarjeta_no_lleva_cambio(self, monkeypatch):
        _, o, _, _ = self._vender(monkeypatch, payment_method="card")
        assert o.amount_received is None and o.change_given is None

    def test_la_venta_queda_ligada_al_turno_y_cobrada(self, monkeypatch):
        _, o, _, _ = self._vender(monkeypatch)
        assert o.cash_session_id == "cs_1" and o.status == "paid" and o.captured_by == "staff"

    def test_el_ticket_dice_quien_cobro(self, monkeypatch):
        r, _, _, _ = self._vender(monkeypatch)
        assert r.cashier == "ana"

    def test_mueve_el_inventario_igual_que_antes(self, monkeypatch):
        _, _, prod, db = self._vender(monkeypatch)
        assert prod.inventory_count == 8 and db.commits == 1


# ═══════════════════════════════════════════════════════════════════
# PS-22 · El corte de caja
# ═══════════════════════════════════════════════════════════════════


def fila(metodo, n, total, descuentos="0", propinas="0"):
    return {"method": metodo, "count": n, "total": D(total), "discounts": D(descuentos), "tips": D(propinas)}


class TestResumenDelTurno:
    def test_separa_por_metodo_de_pago(self):
        s = build_summary({"rows": [fila("cash", 3, "300"), fila("card", 2, "250"), fila("transfer", 1, "80")],
                           "excluded": 0, "tips_cash": D("0")})

        assert (s.cash.count, s.cash.total) == (3, D("300.00"))
        assert (s.card.count, s.card.total) == (2, D("250.00"))
        assert (s.transfer.count, s.transfer.total) == (1, D("80.00"))
        assert s.sales_count == 6 and s.gross == D("630.00")

    def test_un_metodo_desconocido_o_nulo_cae_en_otros(self):
        s = build_summary({"rows": [fila(None, 1, "40"), fila("online", 1, "60")], "excluded": 0, "tips_cash": D("0")})
        assert s.other.total == D("100.00") and s.cash.total == D("0.00")

    def test_propinas_y_descuentos_se_suman(self):
        s = build_summary({"rows": [fila("cash", 1, "115", "10", "15"), fila("card", 1, "100", "0", "20")],
                           "excluded": 0, "tips_cash": D("15")})
        assert (s.discounts, s.tips_total, s.tips_cash) == (D("10.00"), D("35.00"), D("15.00"))

    def test_las_excluidas_se_reportan_aparte(self):
        s = build_summary({"rows": [fila("cash", 2, "200")], "excluded": 3, "tips_cash": D("0")})
        assert s.excluded_count == 3 and s.sales_count == 2

    def test_un_turno_sin_ventas_es_cero_no_error(self):
        s = build_summary({"rows": [], "excluded": 0, "tips_cash": D("0")})
        assert s.sales_count == 0 and s.gross == D("0.00")


class TestEfectivoEsperado:
    def test_es_el_fondo_mas_lo_cobrado_en_efectivo(self):
        s = build_summary({"rows": [fila("cash", 3, "300"), fila("card", 2, "250")], "excluded": 0, "tips_cash": D("0")})
        assert expected_cash(D("500.00"), s) == D("800.00")

    def test_tarjeta_y_transferencia_no_entran_al_cajon(self):
        s = build_summary({"rows": [fila("card", 5, "999"), fila("transfer", 5, "999")], "excluded": 0, "tips_cash": D("0")})
        assert expected_cash(D("200.00"), s) == D("200.00")

    def test_la_propina_en_efectivo_ya_va_en_el_total_cobrado(self):
        """El total de esa venta ya la incluye: no se suma otra vez."""
        s = build_summary({"rows": [fila("cash", 1, "115", "0", "15")], "excluded": 0, "tips_cash": D("15")})
        assert expected_cash(D("0.00"), s) == D("115.00")


class FakeCashRepo:
    """Repositorio de caja en memoria con la misma regla de un turno abierto."""

    def __init__(self, ventas=None):
        self.sessions = []
        self._ventas = ventas or {"rows": [], "excluded": 0, "tips_cash": D("0")}

    def get_open(self, tenant_id, lock=None):
        return next((s for s in self.sessions if s.tenant_id == tenant_id and s.status == "open"), None)

    def get_by_id(self, tenant_id, session_id):
        return next((s for s in self.sessions if s.tenant_id == tenant_id and s.id == session_id), None)

    def create(self, session):
        self.sessions.append(session)
        return session

    def save(self, session):
        return session

    def list_for_tenant(self, tenant_id, page, page_size):
        propios = [s for s in self.sessions if s.tenant_id == tenant_id]
        return propios, len(propios)

    def sales_aggregates(self, session_id):
        return self._ventas

    def emails(self, ids):
        return {i: f"{i}@a.test" for i in ids if i}


@pytest.fixture
def caja(monkeypatch):
    monkeypatch.setattr("cash.service.log_operation", lambda **k: None)

    def _build(ventas=None):
        svc = CashService.__new__(CashService)
        svc.db = FakeSession()
        svc.repo = FakeCashRepo(ventas)
        return svc

    return _build


class TestTurnos:
    def test_abrir_crea_un_turno_con_su_fondo(self, caja, tenant_a_user):
        svc = caja()

        r = svc.open(tenant_a_user, OpenCashRequest(opening_float="500"))

        assert r.status == "open" and r.opening_float == D("500.00")
        assert r.opened_by == "u_aaa@a.test" and r.expected_cash == D("500.00")

    def test_no_se_abren_dos_cajas_a_la_vez(self, caja, tenant_a_user):
        svc = caja()
        svc.open(tenant_a_user, OpenCashRequest(opening_float="100"))

        with pytest.raises(CashSessionAlreadyOpenError):
            svc.open(tenant_a_user, OpenCashRequest(opening_float="100"))

    def test_dos_negocios_tienen_cada_uno_su_caja(self, caja, tenant_a_user, tenant_b_user):
        svc = caja()
        svc.open(tenant_a_user, OpenCashRequest(opening_float="100"))
        svc.open(tenant_b_user, OpenCashRequest(opening_float="100"))   # no choca con la de A

    def test_current_es_none_si_la_caja_esta_cerrada(self, caja, tenant_a_user):
        assert caja().current(tenant_a_user.tenant_id) is None

    def test_current_muestra_las_cifras_en_vivo(self, caja, tenant_a_user):
        svc = caja({"rows": [fila("cash", 2, "250")], "excluded": 0, "tips_cash": D("0")})
        svc.open(tenant_a_user, OpenCashRequest(opening_float="100"))

        r = svc.current(tenant_a_user.tenant_id)

        assert r.expected_cash == D("350.00") and r.summary.cash.count == 2


class TestCierreYDiferencia:
    def _turno_con_ventas(self, caja, user, contado, fondo="500"):
        svc = caja({"rows": [fila("cash", 3, "300"), fila("card", 1, "120")], "excluded": 1, "tips_cash": D("0")})
        svc.open(user, OpenCashRequest(opening_float=fondo))
        return svc, svc.close(user, CloseCashRequest(counted_cash=contado))

    def test_cuadra_cuando_lo_contado_es_lo_esperado(self, caja, tenant_a_user):
        _, r = self._turno_con_ventas(caja, tenant_a_user, "800")   # 500 + 300 en efectivo

        assert r.status == "closed" and r.expected_cash == D("800.00") and r.difference == D("0.00")

    def test_un_faltante_es_diferencia_negativa(self, caja, tenant_a_user):
        _, r = self._turno_con_ventas(caja, tenant_a_user, "770")

        assert r.difference == D("-30.00")

    def test_un_sobrante_es_diferencia_positiva(self, caja, tenant_a_user):
        _, r = self._turno_con_ventas(caja, tenant_a_user, "815.50")

        assert r.difference == D("15.50")

    def test_cierra_sin_efectivo_cuando_todo_fue_con_tarjeta(self, caja, tenant_a_user):
        svc = caja({"rows": [fila("card", 4, "400")], "excluded": 0, "tips_cash": D("0")})
        svc.open(tenant_a_user, OpenCashRequest(opening_float="200"))

        r = svc.close(tenant_a_user, CloseCashRequest(counted_cash="200"))

        assert r.expected_cash == D("200.00") and r.difference == D("0.00")

    def test_el_corte_queda_congelado(self, caja, tenant_a_user):
        """Si una venta se reembolsa después, el corte ya cerrado no cambia."""
        svc, cerrado = self._turno_con_ventas(caja, tenant_a_user, "800")
        svc.repo._ventas = {"rows": [], "excluded": 4, "tips_cash": D("0")}   # ahora "todo se reembolsó"

        otra_vez = svc.get_session(tenant_a_user.tenant_id, cerrado.session_id)

        assert otra_vez.expected_cash == D("800.00") and otra_vez.summary.cash.total == D("300.00")

    def test_guarda_quien_cerro_y_las_notas(self, caja, tenant_a_user):
        svc = caja()
        svc.open(tenant_a_user, OpenCashRequest(opening_float="0"))

        r = svc.close(tenant_a_user, CloseCashRequest(counted_cash="0", notes="  Cambio de turno  "))

        assert r.closed_by == "u_aaa@a.test" and r.notes == "Cambio de turno" and r.closed_at is not None

    def test_cerrar_sin_turno_abierto_es_un_error(self, caja, tenant_a_user):
        with pytest.raises(CashSessionNotFoundError):
            caja().close(tenant_a_user, CloseCashRequest(counted_cash="0"))

    def test_un_turno_cerrado_no_se_puede_cerrar_otra_vez(self, caja, tenant_a_user):
        svc, _ = self._turno_con_ventas(caja, tenant_a_user, "800")

        with pytest.raises(CashSessionNotFoundError):
            svc.close(tenant_a_user, CloseCashRequest(counted_cash="800"))

    def test_despues_de_cerrar_se_puede_abrir_otra(self, caja, tenant_a_user):
        svc, _ = self._turno_con_ventas(caja, tenant_a_user, "800")

        r = svc.open(tenant_a_user, OpenCashRequest(opening_float="300"))

        assert r.status == "open"

    def test_no_se_lee_el_turno_de_otro_negocio(self, caja, tenant_a_user, tenant_b_user):
        svc = caja()
        a = svc.open(tenant_a_user, OpenCashRequest(opening_float="0"))

        with pytest.raises(CashSessionNotFoundError):
            svc.get_session(tenant_b_user.tenant_id, a.session_id)

    def test_el_conteo_no_puede_ser_negativo(self):
        with pytest.raises(ValidationError):
            CloseCashRequest(counted_cash="-1")

    def test_el_historial_lista_los_turnos_del_negocio(self, caja, tenant_a_user, tenant_b_user):
        svc = caja()
        svc.open(tenant_a_user, OpenCashRequest(opening_float="0"))
        svc.open(tenant_b_user, OpenCashRequest(opening_float="0"))

        lista = svc.list_sessions(tenant_a_user.tenant_id, 1, 20)

        assert lista.total == 1


# ═══════════════════════════════════════════════════════════════════
# PS-23 · SKU y código de barras
# ═══════════════════════════════════════════════════════════════════

FINAL = {"categoria": "plato_fuerte", "tiempo_preparacion_min": 10, "picante": False}


def alta(nombre, **codigos):
    return ProductCreate(product_type="final", name=nombre, price="50", metadata=FINAL, **codigos)


class TestCodigosDeProducto:
    def test_se_guardan_al_crear(self, service_factory, tenant_a_user):
        svc = service_factory()

        r = svc.create_product(alta("Taco", sku="TAC-01", barcode="7501234567890"), tenant_a_user)

        assert (r.sku, r.barcode) == ("TAC-01", "7501234567890")

    def test_son_opcionales(self, service_factory, tenant_a_user):
        r = service_factory().create_product(alta("Taco"), tenant_a_user)
        assert r.sku is None and r.barcode is None

    def test_vacios_se_guardan_como_sin_codigo(self):
        assert alta("Taco", sku="   ", barcode="").sku is None

    def test_sku_repetido_en_el_mismo_negocio_se_rechaza(self, service_factory, tenant_a_user):
        svc = service_factory()
        svc.create_product(alta("Taco", sku="TAC-01"), tenant_a_user)

        with pytest.raises(DuplicateProductCodeError) as e:
            svc.create_product(alta("Torta", sku="TAC-01"), tenant_a_user)

        assert e.value.field == "sku"

    def test_el_sku_no_distingue_mayusculas(self, service_factory, tenant_a_user):
        """'ab-01' y 'AB-01' son el mismo para quien teclea."""
        svc = service_factory()
        svc.create_product(alta("Taco", sku="AB-01"), tenant_a_user)

        with pytest.raises(DuplicateProductCodeError):
            svc.create_product(alta("Torta", sku="ab-01"), tenant_a_user)

    def test_codigo_de_barras_repetido_se_rechaza(self, service_factory, tenant_a_user):
        svc = service_factory()
        svc.create_product(alta("Taco", barcode="750123"), tenant_a_user)

        with pytest.raises(DuplicateProductCodeError) as e:
            svc.create_product(alta("Torta", barcode="750123"), tenant_a_user)

        assert e.value.field == "barcode"

    def test_dos_negocios_pueden_usar_el_mismo_codigo(self, service_factory, tenant_a_user, tenant_b_user):
        """Cada negocio tiene su propio catálogo: el aislamiento multi-tenant incluye los códigos."""
        existente = make_product("p_b", "t_bbb", name="Lente")
        existente.sku, existente.barcode = "COD-1", "999"
        svc = service_factory([existente])

        r = svc.create_product(alta("Taco", sku="COD-1", barcode="999"), tenant_a_user)

        assert r.sku == "COD-1"

    def test_un_producto_borrado_libera_su_codigo(self, service_factory, tenant_a_user):
        viejo = make_product("p_old", "t_aaa", name="Viejo")
        viejo.sku, viejo.is_deleted = "TAC-01", True
        svc = service_factory([viejo])

        r = svc.create_product(alta("Taco", sku="TAC-01"), tenant_a_user)

        assert r.sku == "TAC-01"

    @pytest.mark.parametrize("malo", ["con espacio", "ñandú", "a;b", "<script>"])
    def test_formato_invalido(self, malo):
        with pytest.raises(ValidationError):
            alta("Taco", sku=malo)

    def test_largo_maximo(self):
        with pytest.raises(ValidationError):
            alta("Taco", barcode="1" * 65)

    def test_editar_a_un_codigo_ajeno_se_rechaza(self, service_factory, tenant_a_user):
        a = make_product("p_a", "t_aaa", name="A"); a.sku = "UNO"
        b = make_product("p_b", "t_aaa", name="B")
        svc = service_factory([a, b])

        with pytest.raises(DuplicateProductCodeError):
            svc.update_product("p_b", ProductUpdate(sku="uno"), tenant_a_user)

    def test_conservar_su_propio_codigo_no_choca(self, service_factory, tenant_a_user):
        a = make_product("p_a", "t_aaa", name="A"); a.sku = "UNO"
        svc = service_factory([a])

        r = svc.update_product("p_a", ProductUpdate(sku="UNO", price="60"), tenant_a_user)

        assert r.sku == "UNO"

    def test_cadena_vacia_quita_el_codigo(self, service_factory, tenant_a_user):
        a = make_product("p_a", "t_aaa", name="A"); a.sku, a.barcode = "UNO", "123"
        svc = service_factory([a])

        r = svc.update_product("p_a", ProductUpdate(sku=""), tenant_a_user)

        assert r.sku is None and r.barcode == "123"      # solo se quitó el que se pidió

    def test_no_mandar_el_campo_lo_deja_como_estaba(self, service_factory, tenant_a_user):
        a = make_product("p_a", "t_aaa", name="A"); a.sku = "UNO"
        svc = service_factory([a])

        r = svc.update_product("p_a", ProductUpdate(price="75"), tenant_a_user)

        assert r.sku == "UNO"
