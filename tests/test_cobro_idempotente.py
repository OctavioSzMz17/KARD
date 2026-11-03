"""
Pruebas del cobro idempotente.

  PS-07  Cobrar el MISMO ticket dos veces (doble clic, reintento tras perder la
         señal, respuesta que no llegó) no crea otra venta ni vuelve a descontar
         inventario; la misma llave con otro contenido o de otra persona se
         rechaza; y dos peticiones que compiten por la llave no duplican nada.

Igual que el resto de la suite: servicio real con repositorio y sesión de
mentira. La carrera real entre dos conexiones se prueba contra la base de
verdad en `tests/integracion/e2e_idempotencia.py`.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from core import permissions as P
from core.dependencies import CurrentUser, get_current_user
from core.exceptions import CashClosedError, IdempotencyConflictError
from db.database import get_db
from db.models import Order, OrderItem
from main import app
from orders import routes as orders_routes
from orders.idempotency import is_valid_key, request_fingerprint
from tests.test_caja_pos import FakeDb, servicio_de_venta, venta

D = Decimal
LLAVE = "ticket-0001-abcdefgh"          # 20 caracteres: válida


class RepoFalso:
    """Repositorio en memoria de las ventas con llave, con el orden de sus llamadas."""

    def __init__(self, db, tenant=None):
        self.db = db
        self.tenant = tenant or SimpleNamespace(slug="sabor", business_name="Sabor")
        self.llamadas = []

    def lock_idempotency_key(self, tenant_id, key):
        self.llamadas.append(("lock", tenant_id, key))

    def get_by_idempotency_key(self, tenant_id, key):
        self.llamadas.append(("get", tenant_id, key))
        for o in self.db.added:
            if isinstance(o, Order) and o.tenant_id == tenant_id and o.idempotency_key == key:
                return self._como_guardada(o)
        return None

    def _como_guardada(self, o):
        """La venta como la devolvería la base: columnas, partidas y negocio."""
        columnas = {c.key: getattr(o, c.key) for c in Order.__table__.columns}
        partidas = [i for i in self.db.added if isinstance(i, OrderItem) and i.order_id == o.id]
        return SimpleNamespace(**columnas, items=partidas, tenant=self.tenant)


def preparar(monkeypatch, **kw):
    svc, db, prod = servicio_de_venta(monkeypatch, **kw)
    svc.repo = RepoFalso(db)
    return svc, db, prod


def cobrar(svc, *, usuario="u_1", llave=LLAVE, cantidad=2, **extra):
    return svc.create_pos_sale_idempotent(
        "t_aaa", usuario, venta(items=[{"product_id": "p_1", "quantity": cantidad}], **extra),
        cashier="ana", idempotency_key=llave,
    )


def ventas_guardadas(db):
    return [o for o in db.added if isinstance(o, Order)]


# ═══════════════════════════════════════════════════════════════════
# La llave: forma y huella
# ═══════════════════════════════════════════════════════════════════


class TestFormaDeLaLlave:
    @pytest.mark.parametrize("llave", [
        "0123456789abcdef",                                # 16, el mínimo
        "550e8400-e29b-41d4-a716-446655440000",            # un UUID
        "A_b-C_d-0123456789",
        "x" * 80,                                          # el máximo
    ])
    def test_acepta_llaves_razonables(self, llave):
        assert is_valid_key(llave)

    @pytest.mark.parametrize("llave", [
        "", "corta", "x" * 15, "x" * 81,                   # largo
        "con espacios en medio!!", "ticket/0001/abcdef", "ñandú-ñandú-ñandú",
        "ok-pero-con-salto\n", "ok-pero-con-salto\r\nX: y",   # el salto no debe colarse en cabeceras ni registros
        "<script>alert(1)</script>",
    ])
    def test_rechaza_lo_demas(self, llave):
        assert not is_valid_key(llave)


class TestHuellaDeLaPeticion:
    def test_es_estable_aunque_cambie_el_orden_de_las_claves(self):
        a = venta(items=[{"product_id": "p_1", "quantity": 2}], tip="5", payment_method="card")
        b = venta(payment_method="card", tip="5", items=[{"quantity": 2, "product_id": "p_1"}])
        assert request_fingerprint(a, "u_1") == request_fingerprint(b, "u_1")

    @pytest.mark.parametrize("cambio", [
        {"items": [{"product_id": "p_1", "quantity": 3}]},                  # otra cantidad
        {"items": [{"product_id": "p_2", "quantity": 2}]},                  # otro producto
        {"tip": "5"},
        {"discount": {"type": "percent", "value": "10"}},
        {"payment_method": "card"},
        {"fulfillment_type": "pickup"},                                     # el valor por defecto es takeaway
        {"customer_name": "Ana"},
        {"table_number": "4"},
    ])
    def test_cambia_con_cualquier_diferencia_del_ticket(self, cambio):
        base = dict(items=[{"product_id": "p_1", "quantity": 2}])
        assert request_fingerprint(venta(**base), "u_1") != request_fingerprint(venta(**{**base, **cambio}), "u_1")

    def test_cambia_si_cobra_otra_persona(self):
        v = venta()
        assert request_fingerprint(v, "u_1") != request_fingerprint(v, "u_2")


# ═══════════════════════════════════════════════════════════════════
# PS-07 · El mismo cobro dos veces
# ═══════════════════════════════════════════════════════════════════


class TestElMismoCobroDosVeces:
    def test_el_primer_cobro_crea_la_venta_y_no_es_repeticion(self, monkeypatch):
        svc, db, prod = preparar(monkeypatch)

        r, repetida = cobrar(svc)

        assert repetida is False
        assert len(ventas_guardadas(db)) == 1
        orden = ventas_guardadas(db)[0]
        assert orden.idempotency_key == LLAVE
        assert orden.request_hash == request_fingerprint(venta(items=[{"product_id": "p_1", "quantity": 2}]), "u_1")
        assert r.order_id == orden.id and prod.inventory_count == 8

    def test_el_segundo_cobro_devuelve_la_misma_venta_sin_crear_otra(self, monkeypatch):
        svc, db, prod = preparar(monkeypatch)

        primera, _ = cobrar(svc)
        segunda, repetida = cobrar(svc)

        assert repetida is True
        assert segunda.order_id == primera.order_id
        assert segunda.total == primera.total
        assert len(ventas_guardadas(db)) == 1

    def test_el_segundo_cobro_no_vuelve_a_descontar_inventario(self, monkeypatch):
        """Lo que de verdad cuesta un doble cobro: existencias descontadas dos veces."""
        svc, db, prod = preparar(monkeypatch)

        cobrar(svc)
        cobrar(svc)
        cobrar(svc)

        assert prod.inventory_count == 8                      # 10 - 2, una sola vez
        transacciones = [o for o in db.added if o.__class__.__name__ == "InventoryTransaction"]
        assert len(transacciones) == 1

    def test_la_repeticion_conserva_el_desglose_y_pone_al_cajero(self, monkeypatch):
        svc, db, _ = preparar(monkeypatch)
        extras = dict(discount={"type": "percent", "value": "10"}, tip="15", payment_method="cash", amount_received="500")

        primera, _ = cobrar(svc, **extras)
        segunda, _ = cobrar(svc, **extras)

        for campo in ("subtotal", "discount_amount", "tip_amount", "total", "amount_received", "change_given", "payment_method"):
            assert getattr(segunda, campo) == getattr(primera, campo), campo
        assert [(i.product_id, i.quantity) for i in segunda.items] == [("p_1", 2)]
        assert segunda.cashier == "ana"

    def test_sin_llave_cada_cobro_es_una_venta_nueva(self, monkeypatch):
        """Compatibilidad: quien aún no manda llave se comporta como antes."""
        svc, db, prod = preparar(monkeypatch)

        cobrar(svc, llave=None)
        cobrar(svc, llave=None)

        assert len(ventas_guardadas(db)) == 2 and prod.inventory_count == 6
        assert svc.repo.llamadas == []                         # ni siquiera se consulta

    def test_dos_llaves_distintas_son_dos_ventas(self, monkeypatch):
        svc, db, prod = preparar(monkeypatch)

        cobrar(svc, llave="ticket-0001-abcdefgh")
        cobrar(svc, llave="ticket-0002-abcdefgh")

        assert len(ventas_guardadas(db)) == 2 and prod.inventory_count == 6

    def test_primero_se_toma_el_candado_y_despues_se_busca(self, monkeypatch):
        """Sin este orden, dos peticiones simultáneas pasarían juntas la búsqueda."""
        svc, _, _ = preparar(monkeypatch)

        cobrar(svc)

        assert [x[0] for x in svc.repo.llamadas] == ["lock", "get"]
        assert svc.repo.llamadas[0] == ("lock", "t_aaa", LLAVE)


class TestLaMismaLlaveConOtroContenido:
    def test_otro_contenido_se_rechaza_y_no_se_cobra(self, monkeypatch):
        svc, db, prod = preparar(monkeypatch)
        cobrar(svc, cantidad=2)

        with pytest.raises(IdempotencyConflictError):
            cobrar(svc, cantidad=3)

        assert len(ventas_guardadas(db)) == 1 and prod.inventory_count == 8

    def test_otro_importe_tambien(self, monkeypatch):
        svc, _, _ = preparar(monkeypatch)
        cobrar(svc, tip="5")
        with pytest.raises(IdempotencyConflictError):
            cobrar(svc, tip="50")

    def test_otra_persona_no_recibe_la_venta_ajena(self, monkeypatch):
        """Conocer o adivinar la llave no da acceso al ticket de otro cajero."""
        svc, db, _ = preparar(monkeypatch)
        cobrar(svc, usuario="u_1")

        with pytest.raises(IdempotencyConflictError):
            cobrar(svc, usuario="u_2")

        assert len(ventas_guardadas(db)) == 1

    def test_la_llave_de_otro_negocio_no_cuenta(self, monkeypatch):
        """El repositorio busca por (negocio, llave): la misma llave en otro negocio es otra venta."""
        svc, db, _ = preparar(monkeypatch)
        cobrar(svc)

        assert svc.repo.get_by_idempotency_key("t_bbb", LLAVE) is None


class TestLaRepeticionVaAntesQueLaCaja:
    def test_un_reintento_tras_cerrar_la_caja_devuelve_la_venta_ya_hecha(self, monkeypatch):
        """
        El cajero cobró, se cayó la señal, cerró la caja y la pantalla reintentó.
        Debe enterarse de que SÍ se cobró, no de que "la caja está cerrada".
        """
        svc, db, prod = preparar(monkeypatch)
        primera, _ = cobrar(svc)

        class CajaCerrada:
            def __init__(self, _db):
                pass

            def get_open(self, tenant_id, lock=None):
                return None

        monkeypatch.setattr("orders.service.CashRepository", CajaCerrada)

        segunda, repetida = cobrar(svc)

        assert repetida is True and segunda.order_id == primera.order_id

    def test_una_venta_nueva_con_la_caja_cerrada_sigue_rechazandose(self, monkeypatch):
        svc, db, prod = preparar(monkeypatch, caja_abierta=False)
        with pytest.raises(CashClosedError):
            cobrar(svc)
        assert ventas_guardadas(db) == [] and prod.inventory_count == 10


# ═══════════════════════════════════════════════════════════════════
# PS-07 · Dos peticiones que compiten por la llave
# ═══════════════════════════════════════════════════════════════════


class DbConChoque(FakeDb):
    """Al guardar, el índice único ya tiene a la ganadora: la base rechaza el commit."""

    def commit(self):
        raise IntegrityError("INSERT INTO orders", {}, Exception("uq_orders_tenant_idempotency_key"))

    def rollback(self):
        # Como la base: lo que no se confirmó desaparece.
        super().rollback()
        self.added.clear()


class RepoQueVeAlaGanadoraTarde:
    """Al buscar por primera vez la ganadora aún no existe; después de la colisión, sí."""

    def __init__(self, repo_ganadora):
        self.ganadora = repo_ganadora
        self.busquedas = 0

    def lock_idempotency_key(self, tenant_id, key):
        pass

    def get_by_idempotency_key(self, tenant_id, key):
        self.busquedas += 1
        return None if self.busquedas == 1 else self.ganadora.get_by_idempotency_key(tenant_id, key)


class TestCarreraPorLaLlave:
    def _ganadora(self, monkeypatch):
        svc, db, _ = preparar(monkeypatch)
        resp, _ = cobrar(svc)
        return svc.repo, resp

    def test_la_que_pierde_devuelve_la_venta_de_la_ganadora(self, monkeypatch):
        repo_ganadora, ganadora = self._ganadora(monkeypatch)

        svc, db, prod = servicio_de_venta(monkeypatch)
        db_choque = DbConChoque(db.tenant)
        svc.db = db_choque
        svc.repo = RepoQueVeAlaGanadoraTarde(repo_ganadora)

        r, repetida = cobrar(svc)

        assert repetida is True and r.order_id == ganadora.order_id
        assert db_choque.rollbacks == 1                        # su intento se deshizo, sin dejar nada a medias

    def test_un_choque_que_no_es_de_la_llave_se_propaga(self, monkeypatch):
        """Un IntegrityError por otra causa no se disfraza de repetición."""
        svc, db, _ = servicio_de_venta(monkeypatch)
        svc.db = DbConChoque(db.tenant)
        svc.repo = RepoFalso(svc.db)                           # nadie ganó: no hay venta con esa llave

        with pytest.raises(IntegrityError):
            cobrar(svc)

    def test_sin_llave_un_choque_tampoco_se_disfraza(self, monkeypatch):
        svc, db, _ = servicio_de_venta(monkeypatch)
        svc.db = DbConChoque(db.tenant)
        svc.repo = RepoFalso(svc.db)

        with pytest.raises(IntegrityError):
            cobrar(svc, llave=None)


# ═══════════════════════════════════════════════════════════════════
# PS-07 · Lo que ve el cliente HTTP
# ═══════════════════════════════════════════════════════════════════


def _respuesta_de_venta():
    from orders.models import OrderResponse

    return OrderResponse(
        order_id="ord_1", slug="sabor", business_name="Sabor", status="paid",
        fulfillment_type="takeaway", fulfillment_status="preparing",
        subtotal=D("200.00"), shipping_cost=D("0.00"), total=D("200.00"),
        items=[], skipped=[], created_at="2026-11-02T12:00:00Z",
    )


class TestCobroPorHTTP:
    @pytest.fixture
    def cliente(self, monkeypatch):
        """El endpoint real con el servicio sustituido: lo que importa aquí es la cabecera y el estado."""
        visto = {}

        class ServicioFalso:
            repetida = False

            def __init__(self, db):
                pass

            def create_pos_sale_idempotent(self, tenant_id, user_id, payload, cashier=None, idempotency_key=None):
                visto["llave"] = idempotency_key
                return _respuesta_de_venta(), ServicioFalso.repetida

        monkeypatch.setattr(orders_routes, "OrderService", ServicioFalso)
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="u_dueno", tenant_id="t_aaa", business_type="restaurant",
            role="business_admin", email="dueno@example.com", job_title=P.title_from_position(P.OWNER),
        )
        app.dependency_overrides[get_db] = lambda: SimpleNamespace()
        try:
            yield TestClient(app, raise_server_exceptions=False), visto, ServicioFalso
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)

    CUERPO = {"items": [{"product_id": "p_1", "quantity": 2}]}

    def test_un_cobro_nuevo_responde_201_y_pasa_la_llave(self, cliente):
        c, visto, _ = cliente
        r = c.post("/api/v1/orders/business/sales", json=self.CUERPO, headers={"Idempotency-Key": LLAVE})
        assert r.status_code == 201
        assert "idempotent-replay" not in r.headers
        assert visto["llave"] == LLAVE

    def test_una_repeticion_responde_200_y_lo_avisa(self, cliente):
        c, _, Servicio = cliente
        Servicio.repetida = True
        r = c.post("/api/v1/orders/business/sales", json=self.CUERPO, headers={"Idempotency-Key": LLAVE})
        assert r.status_code == 200
        assert r.headers["idempotent-replay"] == "true"
        assert r.json()["order_id"] == "ord_1"

    def test_sin_cabecera_sigue_funcionando(self, cliente):
        c, visto, _ = cliente
        r = c.post("/api/v1/orders/business/sales", json=self.CUERPO)
        assert r.status_code == 201 and visto["llave"] is None

    @pytest.mark.parametrize("llave", ["corta", "x" * 81, "con espacios en medio", "ticket/0001/abcdefgh"])
    def test_una_llave_con_mala_forma_es_422_y_no_llega_al_servicio(self, cliente, llave):
        c, visto, _ = cliente
        r = c.post("/api/v1/orders/business/sales", json=self.CUERPO, headers={"Idempotency-Key": llave})
        assert r.status_code == 422
        assert "llave" not in visto

    def test_el_conflicto_responde_409_con_su_codigo(self, cliente, monkeypatch):
        c, _, Servicio = cliente

        def conflicto(self, *a, **k):
            raise IdempotencyConflictError("ord_1")

        monkeypatch.setattr(Servicio, "create_pos_sale_idempotent", conflicto)
        r = c.post("/api/v1/orders/business/sales", json=self.CUERPO, headers={"Idempotency-Key": LLAVE})
        assert r.status_code == 409
        assert r.json()["code"] == "IDEMPOTENCY_CONFLICT"
