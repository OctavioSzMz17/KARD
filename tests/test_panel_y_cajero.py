"""
Pruebas del lote "primera versión": nombre de quien cobró y de la caja, corte por cajero,
estadísticas reales del Dashboard y mensajes en español.

  PS-09  El ticket dice "Atendió: Eva P." y "Caja: Caja 1"; las vistas internas, el nombre
         completo y el código; el corte suma por cajero; el Dashboard cuenta días del negocio.
  PS-10  Ningún mensaje que llegue a la pantalla está en inglés.

Igual que el resto de la suite: servicio real con repositorio y sesión de mentira. La base de
verdad se prueba en `tests/integracion/e2e_panel.py`.
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cash.models import CashSummary
from cash.service import build_summary
from core import permissions as P
from core.dependencies import CurrentUser, get_current_user
from db.database import get_db
from main import app
from orders import routes as orders_routes
from orders.cashier import full_name, short_name
from orders.service import OrderService
from orders.stats import average_ticket, fill_days, local_today, window_start_utc
from tests.test_caja_pos import servicio_de_venta, venta

D = Decimal
RAIZ = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════
# PS-09 · Cómo se nombra a quien cobró
# ═══════════════════════════════════════════════════════════════════


class TestNombreCorto:
    """Lo que lleva el ticket del cliente: el nombre y la inicial del apellido."""

    @pytest.mark.parametrize("nombre,esperado", [
        ("Eva Prueba", "Eva P."),
        ("Gina Gerente", "Gina G."),
        ("Ana María López Ruiz", "Ana R."),            # primer nombre + inicial del ÚLTIMO apellido
        ("  eva   prueba  ", "Eva P."),                # espacios de más y minúsculas
        ("eva prueba", "Eva P."),
        ("María de la Cruz", "María C."),
        ("Juan", "Juan"),                               # un solo nombre no lleva punto
        ("juan", "Juan"),
        ("Álvaro Ñúñez", "Álvaro Ñ."),                 # acentos y eñes
    ])
    def test_nombre_y_la_inicial_del_apellido(self, nombre, esperado):
        assert short_name(nombre, "x@example.com") == esperado

    @pytest.mark.parametrize("nombre", [None, "", "   "])
    def test_sin_nombre_cae_en_la_parte_local_del_correo(self, nombre):
        assert short_name(nombre, "admin@sabor-norteno.com") == "admin"

    def test_nunca_entrega_el_correo_completo(self):
        assert "@" not in (short_name(None, "eva.prueba@example.com") or "")

    def test_sin_nombre_ni_correo_no_inventa_nada(self):
        assert short_name(None, None) is None
        assert short_name("", "") is None

    def test_no_entrega_el_apellido_completo(self):
        """El cliente se lleva el ticket a su casa: no se le da el nombre completo de una persona del negocio."""
        r = short_name("Eva Prueba Gutiérrez", "x@example.com")
        assert "Prueba" not in r and "Gutiérrez" not in r


class TestNombreCompleto:
    """Lo que ven las vistas internas."""

    def test_el_nombre_completo_sin_espacios_de_mas(self):
        assert full_name("  Eva   Prueba ", "x@example.com") == "Eva Prueba"

    def test_sin_nombre_cae_en_la_parte_local_del_correo(self):
        assert full_name(None, "admin@sabor-norteno.com") == "admin"
        assert full_name("", None) is None


# ═══════════════════════════════════════════════════════════════════
# PS-09 · La venta guarda desde qué caja se cobró
# ═══════════════════════════════════════════════════════════════════


class TestLaVentaGuardaLaCaja:
    def _vender(self, monkeypatch, **kw):
        svc, db, _ = servicio_de_venta(monkeypatch)
        r = svc.create_pos_sale(
            "t_aaa", "u_1", venta(items=[{"product_id": "p_1", "quantity": 1}]),
            cashier="Eva P.", **kw,
        )
        orden = next(o for o in db.added if o.__class__.__name__ == "Order")
        return r, orden

    def test_guarda_el_nombre_de_la_caja_en_la_venta(self, monkeypatch):
        r, orden = self._vender(monkeypatch, register_name="Caja 1")
        assert orden.register_name == "Caja 1"

    def test_y_el_ticket_lo_trae(self, monkeypatch):
        r, _ = self._vender(monkeypatch, register_name="Caja 1")
        assert (r.cashier, r.register_name) == ("Eva P.", "Caja 1")

    def test_sin_caja_el_ticket_no_inventa_una(self, monkeypatch):
        r, orden = self._vender(monkeypatch)
        assert orden.register_name is None and r.register_name is None

    def test_es_una_copia_del_momento_no_una_referencia(self, monkeypatch):
        """Si la caja se renombra o se retira, las ventas de hoy siguen diciendo la de entonces."""
        _, orden = self._vender(monkeypatch, register_name="Caja 1")
        assert isinstance(orden.register_name, str)

    def test_un_reintento_devuelve_la_caja_de_la_venta_original(self, monkeypatch):
        from tests.test_cobro_idempotente import RepoFalso
        svc, db, _ = servicio_de_venta(monkeypatch)
        svc.repo = RepoFalso(db)
        cuerpo = venta(items=[{"product_id": "p_1", "quantity": 1}])
        llave = "ticket-caja-0001-abcd"
        primera, _ = svc.create_pos_sale_idempotent("t_aaa", "u_1", cuerpo, "Eva P.", llave, "Caja 1")
        segunda, repetida = svc.create_pos_sale_idempotent("t_aaa", "u_1", cuerpo, "Eva P.", llave, "OTRO NOMBRE")
        assert repetida and segunda.order_id == primera.order_id
        assert segunda.register_name == "Caja 1"


class TestReporteDeVentasConCajero:
    def _orden(self, **kw):
        base = dict(
            id="ord_1", status="paid", fulfillment_type="takeaway", fulfillment_status="preparing",
            captured_by="staff", payment_method="cash", subtotal=D("100.00"), shipping_cost=D("0.00"),
            discount_amount=D("0.00"), tip_amount=D("0.00"), amount_received=None, change_given=None,
            total=D("100.00"), table_number=None, customer_name=None, consumer_id=None, items=[],
            created_at=datetime(2026, 11, 4, tzinfo=timezone.utc), register_name="Caja 1",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_trae_nombre_completo_codigo_y_caja(self):
        user = SimpleNamespace(full_name="Eva Prueba", email="eva@example.com", employee_code="0009")
        r = OrderService._sale_to_response(self._orden(), None, user)
        assert (r.cashier_name, r.cashier_code, r.register_name) == ("Eva Prueba", "0009", "Caja 1")

    def test_sin_nombre_usa_el_correo_sin_dominio(self):
        user = SimpleNamespace(full_name=None, email="admin@sabor.com", employee_code="0001")
        assert OrderService._sale_to_response(self._orden(), None, user).cashier_name == "admin"

    def test_una_venta_en_linea_no_tiene_cajero(self):
        r = OrderService._sale_to_response(self._orden(register_name=None), None, None)
        assert (r.cashier_name, r.cashier_code, r.register_name) == (None, None, None)

    def test_sin_pasar_al_cajero_sigue_funcionando(self):
        """Compatibilidad: quien llama como antes (sin cajero) no se rompe."""
        assert OrderService._sale_to_response(self._orden(), None).cashier_name is None


# ═══════════════════════════════════════════════════════════════════
# PS-09 · El corte dice cuánto cobró cada cajero
# ═══════════════════════════════════════════════════════════════════


def agregados(**extra):
    base = {
        "rows": [
            {"method": "cash", "count": 3, "total": D("300.00"), "discounts": D("0"), "tips": D("0")},
            {"method": "card", "count": 1, "total": D("120.00"), "discounts": D("0"), "tips": D("0")},
        ],
        "excluded": 0, "tips_cash": D("0"),
    }
    base.update(extra)
    return base


class TestCortePorCajero:
    def test_suma_por_persona(self):
        s = build_summary(agregados(by_cashier=[
            {"user_id": "u_1", "name": "Eva Prueba", "code": "0009", "count": 3, "total": D("300.00")},
            {"user_id": "u_2", "name": "Luis Ramos", "code": "0010", "count": 1, "total": D("120.00")},
        ]))
        assert [(c.name, c.code, c.count, c.total) for c in s.by_cashier] == [
            ("Eva Prueba", "0009", 3, D("300.00")), ("Luis Ramos", "0010", 1, D("120.00"))]

    def test_los_importes_salen_con_dos_decimales(self):
        s = build_summary(agregados(by_cashier=[{"user_id": "u_1", "name": "Eva", "code": None, "count": 1, "total": D("99.5")}]))
        assert str(s.by_cashier[0].total) == "99.50"

    def test_lo_que_suma_cada_cajero_cuadra_con_el_total_del_turno(self):
        s = build_summary(agregados(by_cashier=[
            {"user_id": "u_1", "name": "Eva", "code": "1", "count": 3, "total": D("300.00")},
            {"user_id": "u_2", "name": "Luis", "code": "2", "count": 1, "total": D("120.00")},
        ]))
        assert sum(c.total for c in s.by_cashier) == s.gross and sum(c.count for c in s.by_cashier) == s.sales_count

    def test_sin_ventas_no_hay_lista(self):
        assert build_summary(agregados(rows=[], by_cashier=[])).by_cashier == []

    def test_los_agregados_de_antes_siguen_valiendo(self):
        """Quien arma el resumen sin la lista (código anterior) no se rompe."""
        assert build_summary(agregados()).by_cashier == []

    def test_un_corte_ya_cerrado_antes_de_esto_se_lee_sin_la_lista(self):
        """El resumen congelado de un turno viejo no trae `by_cashier`: se lee igual, con la lista vacía."""
        viejo = {"sales_count": 4, "gross": "420.00", "excluded_count": 0}
        assert CashSummary.model_validate(viejo).by_cashier == []

    def test_un_corte_cerrado_conserva_los_cajeros_congelados(self):
        s = build_summary(agregados(by_cashier=[{"user_id": "u_1", "name": "Eva", "code": "9", "count": 1, "total": D("10.00")}]))
        otra_vez = CashSummary.model_validate(s.model_dump(mode="json"))
        assert otra_vez.by_cashier[0].name == "Eva" and otra_vez.by_cashier[0].total == D("10.00")


# ═══════════════════════════════════════════════════════════════════
# PS-09 · El Dashboard cuenta días del negocio, no de Greenwich
# ═══════════════════════════════════════════════════════════════════


NOV_3_9_30_PM_CDMX = datetime(2026, 11, 4, 3, 30, tzinfo=timezone.utc)      # 21:30 del 3 de noviembre en México (UTC-6)


class TestDiasDelNegocio:
    def test_una_venta_de_las_9_de_la_noche_sigue_siendo_de_hoy(self):
        assert local_today(NOV_3_9_30_PM_CDMX, 360) == date(2026, 11, 3)

    def test_en_utc_ya_es_manana(self):
        assert local_today(NOV_3_9_30_PM_CDMX, 0) == date(2026, 11, 4)

    def test_al_oriente_de_utc_el_dia_se_adelanta(self):
        assert local_today(datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc), -120) == date(2026, 11, 4)

    def test_la_ventana_empieza_a_medianoche_local_del_primer_dia(self):
        """Siete días con hoy 3 de nov en México: del 28 de oct a las 00:00 locales = 06:00 UTC."""
        inicio = window_start_utc(date(2026, 11, 3), 7, 360)
        assert inicio == datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)

    def test_sin_desfase_la_ventana_empieza_a_medianoche_utc(self):
        assert window_start_utc(date(2026, 11, 4), 7, 0) == datetime(2026, 10, 29, 0, 0, tzinfo=timezone.utc)

    def test_un_dia_es_solo_hoy(self):
        assert window_start_utc(date(2026, 11, 3), 1, 360) == datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)


class TestRellenarDias:
    def test_un_renglon_por_dia_del_mas_viejo_a_hoy(self):
        filas = fill_days({}, date(2026, 11, 3), 7)
        assert [f["date"] for f in filas] == [f"2026-10-{d}" for d in (28, 29, 30, 31)] + ["2026-11-01", "2026-11-02", "2026-11-03"]

    def test_los_dias_sin_ventas_vienen_en_cero(self):
        """Una gráfica que se salta los días vacíos miente: un día sin ventas es un dato."""
        filas = fill_days({date(2026, 11, 2): (D("150"), 2)}, date(2026, 11, 3), 3)
        assert [(f["revenue"], f["orders"]) for f in filas] == [(D("0.00"), 0), (D("150.00"), 2), (D("0.00"), 0)]

    def test_los_importes_salen_con_dos_decimales(self):
        assert str(fill_days({date(2026, 11, 3): (D("10.5"), 1)}, date(2026, 11, 3), 1)[0]["revenue"]) == "10.50"

    def test_un_dia_fuera_de_la_ventana_no_se_cuela(self):
        filas = fill_days({date(2026, 1, 1): (D("999"), 9)}, date(2026, 11, 3), 7)
        assert sum(f["orders"] for f in filas) == 0

    def test_ticket_promedio(self):
        assert average_ticket(D("150.00"), 2) == D("75.00")
        assert average_ticket(D("100.00"), 3) == D("33.33")

    def test_sin_ventas_no_hay_promedio_ni_division_entre_cero(self):
        assert average_ticket(D("0"), 0) is None


class RepoDeEstadisticas:
    def __init__(self, por_dia=None, top=None):
        self.por_dia, self.top, self.llamadas = por_dia or {}, top or [], []

    def daily_sales(self, tenant_id, since_utc, tz_offset_minutes):
        self.llamadas.append(("dias", tenant_id, since_utc, tz_offset_minutes))
        return self.por_dia

    def top_products(self, tenant_id, since_utc, limit=5):
        self.llamadas.append(("top", tenant_id, since_utc))
        return self.top


@pytest.fixture
def estadisticas(monkeypatch):
    class RelojFijo(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOV_3_9_30_PM_CDMX

    monkeypatch.setattr("orders.service.datetime", RelojFijo)

    def _build(**kw):
        svc = OrderService.__new__(OrderService)
        svc.repo = RepoDeEstadisticas(**kw)
        return svc

    return _build


class TestEstadisticasDelServicio:
    def test_siete_dias_con_los_del_negocio_y_hoy_al_final(self, estadisticas):
        r = estadisticas().stats("t_aaa", 7, 360)
        assert (r.days[0].date, r.days[-1].date) == ("2026-10-28", "2026-11-03")

    def test_pregunta_a_la_base_desde_la_medianoche_local_y_con_el_desfase_del_navegador(self, estadisticas):
        svc = estadisticas()
        svc.stats("t_aaa", 7, 360)
        assert svc.repo.llamadas[0] == ("dias", "t_aaa", datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc), 360)
        assert svc.repo.llamadas[1][2] == datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)

    def test_totales_y_ticket_promedio(self, estadisticas):
        r = estadisticas(por_dia={date(2026, 11, 3): (D("100.00"), 1), date(2026, 11, 1): (D("50.50"), 2)}).stats("t_aaa", 7, 360)
        assert (r.total_revenue, r.total_orders, r.average_ticket) == (D("150.50"), 3, D("50.17"))

    def test_sin_ventas_todo_en_cero_y_sin_promedio(self, estadisticas):
        r = estadisticas().stats("t_aaa", 7, 360)
        assert (r.total_revenue, r.total_orders, r.average_ticket) == (D("0.00"), 0, None)
        assert all(d.revenue == 0 and d.orders == 0 for d in r.days) and r.top_products == []

    def test_lo_mas_vendido_viaja_tal_cual(self, estadisticas):
        top = [{"product_id": "p_1", "name": "Café", "units": 9, "revenue": D("342.00")}]
        r = estadisticas(top=top).stats("t_aaa", 7, 360)
        assert (r.top_products[0].name, r.top_products[0].units, r.top_products[0].revenue) == ("Café", 9, D("342.00"))

    def test_el_negocio_viene_del_token_y_llega_a_las_dos_consultas(self, estadisticas):
        svc = estadisticas()
        svc.stats("t_otro", 7, 0)
        assert {c[1] for c in svc.repo.llamadas} == {"t_otro"}


class FalsoServicio:
    llamadas: list = []

    def __init__(self, db):
        pass

    def stats(self, tenant_id, days, tz_offset):
        FalsoServicio.llamadas.append((tenant_id, days, tz_offset))
        return {"days": [], "top_products": [], "total_revenue": "0.00", "total_orders": 0, "average_ticket": None}


@pytest.fixture
def api(monkeypatch):
    FalsoServicio.llamadas = []
    monkeypatch.setattr(orders_routes, "OrderService", FalsoServicio)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u_g", tenant_id="t_aaa", business_type="restaurant", role="staff",
        email="g@example.com", job_title=P.title_from_position(P.MANAGER),
    )
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


class TestEstadisticasPorHTTP:
    def test_por_defecto_siete_dias_y_sin_desfase(self, api):
        r = api.get("/api/v1/orders/business/stats")
        assert r.status_code == 200 and FalsoServicio.llamadas == [("t_aaa", 7, 0)]

    def test_pasa_los_dias_y_el_desfase_del_navegador(self, api):
        api.get("/api/v1/orders/business/stats", params={"days": 14, "tz_offset": 360})
        assert FalsoServicio.llamadas == [("t_aaa", 14, 360)]

    @pytest.mark.parametrize("params", [{"days": 0}, {"days": 32}, {"days": -1}, {"tz_offset": 841}, {"tz_offset": -841}, {"days": "x"}])
    def test_valores_fuera_de_rango_son_422_y_no_llegan_al_servicio(self, api, params):
        assert api.get("/api/v1/orders/business/stats", params=params).status_code == 422
        assert FalsoServicio.llamadas == []

    def test_no_es_capturado_por_la_ruta_de_un_pedido(self, api):
        """`/business/stats` va antes de `/{order_id}`: no se confunde con un identificador de pedido."""
        assert api.get("/api/v1/orders/business/stats").json()["total_orders"] == 0

    def test_un_empleado_no_ve_las_ventas_del_negocio(self, api):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="u_e", tenant_id="t_aaa", business_type="restaurant", role="staff",
            email="e@example.com", job_title=None,
        )
        assert api.get("/api/v1/orders/business/stats").status_code == 403
        assert FalsoServicio.llamadas == []


# ═══════════════════════════════════════════════════════════════════
# PS-10 · Nada en inglés llega a la pantalla
# ═══════════════════════════════════════════════════════════════════

#: Palabras que delatan un mensaje en inglés (no aparecen en los mensajes en español).
INGLES = re.compile(
    r"\b(not found|already|invalid|denied|unavailable|temporarily|must contain|cannot be|required|"
    r"could not|has been|insufficient|only|is closed|failed|please|the|with this|permissions)\b",
    re.IGNORECASE,
)


def _mensajes(ruta: str, patron: str) -> list[str]:
    return re.findall(patron, (RAIZ / ruta).read_text(encoding="utf-8"))


class TestMensajesEnEspanol:
    def test_los_manejadores_de_error_del_servidor(self):
        mensajes = _mensajes("main.py", r'_error\(\s*"([^"]+)"')
        assert len(mensajes) >= 10
        assert [m for m in mensajes if INGLES.search(m)] == []

    def test_los_errores_de_sesion_y_permisos(self):
        mensajes = _mensajes("core/dependencies.py", r'detail="([^"]+)"')
        assert len(mensajes) >= 3
        assert [m for m in mensajes if INGLES.search(m)] == []

    @pytest.mark.parametrize("ruta", ["auth/models.py", "consumers/models.py", "products/models.py"])
    def test_las_validaciones_de_los_formularios(self, ruta):
        mensajes = _mensajes(ruta, r'raise ValueError\(\s*"([^"]+)"')
        assert [m for m in mensajes if INGLES.search(m)] == []

    @pytest.mark.parametrize("ruta", ["auth/service.py", "cart/service.py", "orders/service.py", "products/service.py"])
    def test_los_errores_de_los_servicios(self, ruta):
        mensajes = _mensajes(ruta, r'(?:UserCreationNotAllowedError|CartUnavailableError|OrderNotFoundError|InsufficientStockError)\(\s*f?"([^"]+)"')
        assert [m for m in mensajes if INGLES.search(m)] == []

    def test_el_correo_repetido_lo_reconoce_el_cliente_por_su_texto_en_espanol(self):
        """El formulario de registro detecta este mensaje: si cambia, hay que cambiar el cliente."""
        assert 'Ese correo ya está registrado' in (RAIZ / "main.py").read_text(encoding="utf-8")
