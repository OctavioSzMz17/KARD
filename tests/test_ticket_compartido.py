"""
Pruebas del ticket compartido entre la caja y el celular.

  PS-08  Lo que se escanea en el celular aparece en la computadora de la misma caja
         (y al revés), sin que dos pantallas se pisen, sin perder el ticket si Redis
         falla y sin que nadie vea o toque la caja de otro.

Igual que el resto de la suite: servicio real con almacenes en memoria. La base de
verdad se prueba en `tests/integracion/e2e_ticket.py`.
"""

import copy
from types import SimpleNamespace

import pytest
import redis
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core import permissions as P
from core.dependencies import get_current_user
from core.exceptions import TicketBusyError, TicketFullError
from db.database import get_db
from devices.dependencies import get_optional_device
from main import app
from orders.idempotency import is_valid_key
from tests.conftest import FakeSession
from tests.test_dispositivos import (  # noqa: F401  (`servicio` es un fixture)
    AHORA, FakeDeviceRepository, FakeTicketService, HASH, SECRETO, aprobar, equipo_autorizado, fila, servicio, usuario,
)
from devices.models import DeviceLoginRequest
from tickets import routes as tickets_routes
from tickets.logic import (
    MAX_LINES, MAX_QUANTITY, OPS_MEMORY, apply_op, empty_state, placeholder_state, public,
)
from tickets.models import TicketOp
from tickets.repository import TTL_SECONDS, RedisDraftStore
from tickets.service import TicketService
from tickets import service as tickets_service_module

_n = [0]


def op(tipo, producto=None, cantidad=None, uid=None, op_id=None):
    """Una operación con llave propia; `op_id` fijo para probar repeticiones."""
    _n[0] += 1
    d = {"op_id": op_id or f"op-{_n[0]:016d}", "type": tipo}
    if producto is not None:
        d["product_id"] = producto
    if cantidad is not None:
        d["quantity"] = cantidad
    if uid is not None:
        d["uid"] = uid
    return d


def aplicar(estado, *operaciones):
    for o in operaciones:
        estado, _ = apply_op(estado, o)
    return estado


def cantidades(estado):
    return {l["product_id"]: l["quantity"] for l in estado["lines"]}


# ═══════════════════════════════════════════════════════════════════
# Las reglas del ticket
# ═══════════════════════════════════════════════════════════════════


class TestAgregar:
    def test_un_producto_nuevo_entra_con_su_cantidad(self):
        e, cambio = apply_op(empty_state(), op("add", "p_1", 3))
        assert cambio and cantidades(e) == {"p_1": 3} and e["rev"] == 1

    def test_sin_cantidad_es_una_unidad(self):
        assert cantidades(aplicar(empty_state(), op("add", "p_1"))) == {"p_1": 1}

    def test_el_mismo_producto_suma_no_duplica_la_linea(self):
        """El celular y la laptop escanean el mismo producto: son dos unidades, no una."""
        e = aplicar(empty_state(), op("add", "p_1"), op("add", "p_1"))
        assert cantidades(e) == {"p_1": 2} and len(e["lines"]) == 1

    def test_la_cantidad_no_pasa_de_999(self):
        e = aplicar(empty_state(), op("add", "p_1", 998), op("add", "p_1", 50))
        assert cantidades(e) == {"p_1": MAX_QUANTITY}

    def test_agregar_cuando_ya_esta_al_tope_no_cambia_nada(self):
        e = aplicar(empty_state(), op("add", "p_1", MAX_QUANTITY))
        e2, cambio = apply_op(e, op("add", "p_1"))
        assert not cambio and e2 is e

    def test_hay_un_maximo_de_productos_distintos(self):
        e = aplicar(empty_state(), *[op("add", f"p_{i}") for i in range(MAX_LINES)])
        with pytest.raises(TicketFullError):
            apply_op(e, op("add", "p_nuevo"))

    def test_con_el_maximo_todavia_se_puede_sumar_a_un_producto_que_ya_esta(self):
        e = aplicar(empty_state(), *[op("add", f"p_{i}") for i in range(MAX_LINES)])
        e2, cambio = apply_op(e, op("add", "p_0"))
        assert cambio and cantidades(e2)["p_0"] == 2


class TestCambiarYQuitar:
    def test_cambiar_la_cantidad(self):
        e = aplicar(empty_state(), op("add", "p_1", 2), op("set_quantity", "p_1", 7))
        assert cantidades(e) == {"p_1": 7}

    def test_cambiar_la_cantidad_de_algo_que_ya_no_esta_no_es_un_error(self):
        """Lo borraron en la otra pantalla un instante antes: no hay nada que cambiar."""
        e, cambio = apply_op(empty_state(), op("set_quantity", "p_1", 5))
        assert not cambio and e["lines"] == []

    def test_quitar(self):
        e = aplicar(empty_state(), op("add", "p_1"), op("add", "p_2"), op("remove", "p_1"))
        assert cantidades(e) == {"p_2": 1}

    def test_quitar_algo_que_ya_no_esta_no_cambia_nada(self):
        e = aplicar(empty_state(), op("add", "p_1"))
        e2, cambio = apply_op(e, op("remove", "p_9"))
        assert not cambio and e2 is e

    def test_borrar_en_una_pantalla_mientras_la_otra_agrega_conserva_las_dos_cosas(self):
        e = aplicar(empty_state(), op("add", "p_1"), op("add", "p_2"))
        # la laptop borra p_1 y, casi a la vez, el celular escanea p_3
        a = aplicar(e, op("remove", "p_1"), op("add", "p_3"))
        b = aplicar(e, op("add", "p_3"), op("remove", "p_1"))
        assert cantidades(a) == cantidades(b) == {"p_2": 1, "p_3": 1}


class TestVaciar:
    def test_vaciar_deja_el_ticket_en_blanco_y_le_pone_otro_identificador(self):
        e = aplicar(empty_state(), op("add", "p_1"))
        viejo = e["uid"]
        e2, cambio = apply_op(e, op("clear"))
        assert cambio and e2["lines"] == [] and e2["uid"] != viejo

    def test_vaciar_con_el_identificador_correcto(self):
        e = aplicar(empty_state(), op("add", "p_1"))
        e2, cambio = apply_op(e, op("clear", uid=e["uid"]))
        assert cambio and e2["lines"] == []

    def test_un_vaciar_tardio_de_un_ticket_ya_cobrado_no_borra_el_siguiente(self):
        """
        La laptop cobra el ticket A y lo vacía. Un segundo después el celular, que aún no se
        enteraba, manda su propio "vaciar A": el ticket B que ya se está armando no debe tocarse.
        """
        a = aplicar(empty_state(), op("add", "p_1"))
        uid_a = a["uid"]
        b = aplicar(apply_op(a, op("clear", uid=uid_a))[0], op("add", "p_2"))
        b2, cambio = apply_op(b, op("clear", uid=uid_a))
        assert not cambio and cantidades(b2) == {"p_2": 1}


class TestRepeticionesYRevision:
    def test_una_operacion_repetida_no_se_aplica_dos_veces(self):
        o = op("add", "p_1", op_id="repetida-0000000001")
        e = aplicar(empty_state(), o)
        e2, cambio = apply_op(e, o)
        assert not cambio and cantidades(e2) == {"p_1": 1} and e2["rev"] == 1

    def test_la_revision_sube_solo_cuando_algo_cambia(self):
        e = empty_state()
        e, _ = apply_op(e, op("add", "p_1"))
        e, _ = apply_op(e, op("remove", "p_9"))              # no cambia nada
        e, _ = apply_op(e, op("set_quantity", "p_1", 4))
        assert e["rev"] == 2

    def test_no_modifica_el_estado_que_recibe(self):
        e = aplicar(empty_state(), op("add", "p_1"))
        copia = copy.deepcopy(e)
        apply_op(e, op("add", "p_2"))
        apply_op(e, op("set_quantity", "p_1", 9))
        apply_op(e, op("clear"))
        assert e == copia

    def test_solo_se_recuerdan_las_ultimas_llaves(self):
        e = aplicar(empty_state(), *[op("add", f"p_{i}") for i in range(OPS_MEMORY + 10)])
        assert len(e["ops"]) == OPS_MEMORY

    def test_lo_que_ven_las_pantallas_no_incluye_la_memoria_de_llaves(self):
        e = aplicar(empty_state(), op("add", "p_1"))
        assert set(public(e)) == {"uid", "rev", "lines"}

    def test_el_identificador_sirve_como_llave_de_idempotencia_del_cobro(self):
        assert is_valid_key(empty_state()["uid"])
        assert is_valid_key(placeholder_state("dv_0123456789abcdef")["uid"])

    def test_dos_tickets_nunca_comparten_identificador(self):
        assert len({empty_state()["uid"] for _ in range(500)}) == 500

    def test_la_caja_sin_ticket_muestra_siempre_el_mismo_identificador(self):
        assert placeholder_state("dv_1") == placeholder_state("dv_1")


class TestFormaDeLaOperacion:
    def test_agregar_pide_el_producto(self):
        with pytest.raises(ValidationError):
            TicketOp(op_id="op-0000000000000001", type="add")

    def test_cambiar_cantidad_pide_producto_y_cantidad(self):
        with pytest.raises(ValidationError):
            TicketOp(op_id="op-0000000000000001", type="set_quantity", product_id="p_1")
        with pytest.raises(ValidationError):
            TicketOp(op_id="op-0000000000000001", type="set_quantity", quantity=2)

    def test_vaciar_no_pide_nada_mas(self):
        assert TicketOp(op_id="op-0000000000000001", type="clear").product_id is None

    @pytest.mark.parametrize("cantidad", [0, -1, 1000])
    def test_cantidades_fuera_de_rango(self, cantidad):
        with pytest.raises(ValidationError):
            TicketOp(op_id="op-0000000000000001", type="add", product_id="p_1", quantity=cantidad)

    @pytest.mark.parametrize("llave", ["corta", "x" * 65, "con espacios en medio!!", "salto\nde-linea-000001"])
    def test_la_llave_de_operacion_tiene_forma(self, llave):
        with pytest.raises(ValidationError):
            TicketOp(op_id=llave, type="clear")

    def test_un_tipo_inventado_se_rechaza(self):
        with pytest.raises(ValidationError):
            TicketOp(op_id="op-0000000000000001", type="regalar", product_id="p_1")


# ═══════════════════════════════════════════════════════════════════
# El servicio: Redis vivo, base como copia
# ═══════════════════════════════════════════════════════════════════


class FakeRedis:
    """Lo mínimo de redis-py que usa el ticket, con el WATCH/MULTI de verdad (una escritura ajena aborta)."""

    def __init__(self):
        self.data, self.ttl, self.versions = {}, {}, {}
        self.before_execute = None      # simula a otra petición que escribe justo antes de confirmar
        self.fail = None                # excepción que lanza cualquier operación

    def _check(self):
        if self.fail:
            raise self.fail

    def get(self, key):
        self._check()
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.data:
            return None
        self.data[key], self.ttl[key] = value, ex
        self.versions[key] = self.versions.get(key, 0) + 1
        return True

    def delete(self, *keys):
        self._check()
        for k in keys:
            self.data.pop(k, None)

    def scan_iter(self, match=None):
        self._check()
        prefijo = (match or "").rstrip("*")
        return iter([k for k in list(self.data) if k.startswith(prefijo)])

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, r):
        self.r, self.watched, self.queue = r, {}, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def watch(self, key):
        self.watched[key] = self.r.versions.get(key, 0)

    def get(self, key):
        return self.r.get(key)

    def multi(self):
        pass

    def set(self, key, value, ex=None):
        self.queue.append((key, value, ex))

    def execute(self):
        if self.r.before_execute:
            self.r.before_execute()
        for key, version in self.watched.items():
            if self.r.versions.get(key, 0) != version:
                raise redis.WatchError()
        for key, value, ex in self.queue:
            self.r.set(key, value, ex=ex)


class FakePg:
    """La copia en la base, en memoria."""

    def __init__(self):
        self.rows, self.snapshots = {}, []

    def load(self, t, d):
        row = self.rows.get((t, d))
        return copy.deepcopy(row) if row else None

    def update(self, t, d, fn):
        state = self.rows.get((t, d)) or empty_state()
        new, changed = fn(copy.deepcopy(state))
        if changed:
            self.rows[(t, d)] = new
            return new, True
        return state, False

    def save_snapshot(self, t, d, state):
        actual = self.rows.get((t, d))
        if actual is None or actual["rev"] < state["rev"]:
            self.rows[(t, d)] = copy.deepcopy(state)
            self.snapshots.append(state["rev"])

    def delete(self, t, d):
        self.rows.pop((t, d), None)

    def delete_tenant(self, t):
        for k in [k for k in self.rows if k[0] == t]:
            del self.rows[k]


def servicio_de_tickets(con_redis=True):
    svc = TicketService.__new__(TicketService)
    svc.db = FakeSession()
    svc.pg = FakePg()
    svc.redis = None
    fake = None
    if con_redis:
        fake = FakeRedis()
        svc.redis = RedisDraftStore(fake)
    return svc, fake


T, D = "t_aaa", "dv_1"
CLAVE = f"ticket:{T}:{D}"


class TestGuardadoConRedis:
    def test_el_ticket_vivo_esta_en_redis_con_caducidad(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        assert CLAVE in r.data
        assert r.ttl[CLAVE] == TTL_SECONDS == 12 * 3600

    def test_cada_cambio_renueva_la_caducidad(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        r.ttl[CLAVE] = 5
        svc.apply(T, D, op("add", "p_2"))
        assert r.ttl[CLAVE] == TTL_SECONDS

    def test_cada_cambio_deja_una_copia_en_la_base(self):
        svc, _ = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        svc.apply(T, D, op("add", "p_2"))
        assert svc.pg.rows[(T, D)]["rev"] == 2 and svc.pg.snapshots == [1, 2]

    def test_un_cambio_que_no_cambia_nada_no_escribe_copia(self):
        svc, _ = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        svc.apply(T, D, op("remove", "p_9"))
        assert svc.pg.snapshots == [1]

    def test_la_clave_lleva_el_negocio_y_la_caja(self):
        svc, r = servicio_de_tickets()
        svc.apply("t_aaa", "dv_1", op("add", "p_1"))
        assert list(r.data) == ["ticket:t_aaa:dv_1"]

    def test_otro_negocio_no_ve_el_ticket_aunque_adivine_el_identificador_de_la_caja(self):
        svc, _ = servicio_de_tickets()
        svc.apply("t_aaa", "dv_1", op("add", "p_1"))
        assert svc.current("t_bbb", "dv_1")["lines"] == []
        assert svc.current("t_aaa", "dv_1")["lines"] == [{"product_id": "p_1", "quantity": 1}]

    def test_lee_y_devuelve_el_ticket_completo(self):
        svc, _ = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1", 2))
        e = svc.current(T, D)
        assert e["rev"] == 1 and cantidades(e) == {"p_1": 2}

    def test_una_caja_sin_ticket_devuelve_el_ticket_en_blanco_estable(self):
        svc, r = servicio_de_tickets()
        assert svc.current(T, D) == svc.current(T, D) == placeholder_state(D)
        assert r.data == {}                       # consultar no crea nada

    def test_confirma_en_la_base(self):
        svc, _ = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        assert svc.db.commits == 1


class TestChoquesEntrePantallas:
    def test_dos_cambios_a_la_vez_se_conservan_los_dos(self):
        """El celular y la laptop cambian el mismo ticket en el mismo instante: nada se pierde."""
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))

        def otra_pantalla():
            r.before_execute = None                              # solo se cuela una vez
            svc.apply(T, D, op("add", "p_2"))

        r.before_execute = otra_pantalla
        svc.apply(T, D, op("add", "p_3"))
        assert cantidades(svc.current(T, D)) == {"p_1": 1, "p_2": 1, "p_3": 1}
        assert svc.current(T, D)["rev"] == 3

    def test_si_otra_escribe_sin_parar_se_rinde_con_un_error_que_se_puede_reintentar(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        r.before_execute = lambda: r.set(CLAVE, r.data[CLAVE])    # cada intento choca
        with pytest.raises(TicketBusyError):
            svc.apply(T, D, op("add", "p_2"))

    def test_un_ticket_lleno_no_guarda_nada(self):
        svc, r = servicio_de_tickets()
        for i in range(MAX_LINES):
            svc.apply(T, D, op("add", f"p_{i}"))
        antes = r.data[CLAVE]
        with pytest.raises(TicketFullError):
            svc.apply(T, D, op("add", "p_extra"))
        assert r.data[CLAVE] == antes


class TestRecuperacion:
    def test_si_redis_se_reinicia_el_ticket_se_recupera_de_la_base(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1", 2))
        r.data.clear()                                           # Redis perdió todo
        assert cantidades(svc.current(T, D)) == {"p_1": 2}
        assert CLAVE in r.data                                   # y se volvió a cargar

    def test_y_el_siguiente_cambio_continua_donde_iba(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1", 2))
        r.data.clear()
        e = svc.apply(T, D, op("add", "p_2"))
        assert cantidades(e) == {"p_1": 2, "p_2": 1} and e["rev"] == 2

    def test_la_recuperacion_no_pisa_lo_que_ya_hay_en_redis(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        svc.pg.rows[(T, D)]["lines"] = []                        # copia atrasada
        assert cantidades(svc.current(T, D)) == {"p_1": 1}

    def test_si_redis_falla_al_leer_se_lee_de_la_base(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        r.fail = redis.ConnectionError("caído")
        assert cantidades(svc.current(T, D)) == {"p_1": 1}

    def test_si_redis_falla_al_guardar_se_sigue_con_la_base(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        r.fail = redis.ConnectionError("caído")
        e = svc.apply(T, D, op("add", "p_2"))
        assert cantidades(e) == {"p_1": 1, "p_2": 1}
        assert cantidades(svc.pg.load(T, D)) == {"p_1": 1, "p_2": 1}

    def test_sin_redis_la_base_es_el_unico_almacen(self):
        svc, _ = servicio_de_tickets(con_redis=False)
        svc.apply(T, D, op("add", "p_1"))
        svc.apply(T, D, op("add", "p_1"))
        assert cantidades(svc.current(T, D)) == {"p_1": 2}
        assert svc.db.commits == 2

    def test_una_copia_atrasada_no_pisa_una_mas_nueva(self):
        svc, _ = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        svc.apply(T, D, op("add", "p_2"))
        svc.pg.save_snapshot(T, D, {"uid": "x", "rev": 1, "lines": [], "ops": []})
        assert svc.pg.rows[(T, D)]["rev"] == 2


class TestSoltarElTicket:
    def test_vaciar_una_caja_borra_redis_y_la_copia(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        svc.clear(T, D)
        assert CLAVE not in r.data and svc.pg.load(T, D) is None

    def test_vaciar_una_caja_no_toca_las_otras(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, "dv_1", op("add", "p_1"))
        svc.apply(T, "dv_2", op("add", "p_2"))
        svc.clear(T, "dv_1")
        assert cantidades(svc.current(T, "dv_2")) == {"p_2": 1}

    def test_cerrar_la_caja_vacia_las_cajas_del_negocio_y_solo_las_de_ese_negocio(self):
        svc, r = servicio_de_tickets()
        svc.apply("t_aaa", "dv_1", op("add", "p_1"))
        svc.apply("t_aaa", "dv_2", op("add", "p_1"))
        svc.apply("t_bbb", "dv_9", op("add", "p_1"))
        svc.clear_tenant("t_aaa")
        assert list(r.data) == ["ticket:t_bbb:dv_9"]
        assert svc.pg.load("t_aaa", "dv_1") is None and svc.pg.load("t_bbb", "dv_9") is not None

    def test_si_redis_falla_al_borrar_la_copia_igual_se_borra(self):
        svc, r = servicio_de_tickets()
        svc.apply(T, D, op("add", "p_1"))
        r.fail = redis.ConnectionError("caído")
        svc.clear(T, D)
        assert svc.pg.load(T, D) is None


# ═══════════════════════════════════════════════════════════════════
# Qué caja ve cada sesión
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def con_cajas(monkeypatch):
    """TicketService.register_for con el repositorio de equipos en memoria."""

    def _build(*devices):
        repo = FakeDeviceRepository(list(devices))
        monkeypatch.setattr(tickets_service_module, "DeviceRepository", lambda db: repo)
        svc = TicketService.__new__(TicketService)
        svc.db = FakeSession()
        return svc, repo

    return _build


class TestQueCajaVeCadaSesion:
    def test_la_computadora_de_la_caja_ve_la_suya_por_su_token(self, con_cajas):
        caja = equipo_autorizado()
        svc, _ = con_cajas(caja)
        assert svc.register_for(usuario(P.EMPLOYEE), caja) is caja

    def test_el_celular_ve_la_caja_que_su_dueno_opera(self, con_cajas):
        caja = equipo_autorizado()
        caja.operator_id, caja.operator_since = "u_eva", AHORA
        svc, _ = con_cajas(caja)
        assert svc.register_for(usuario(P.EMPLOYEE, user_id="u_eva"), None) is caja

    def test_el_celular_de_otra_persona_no_ve_esa_caja(self, con_cajas):
        caja = equipo_autorizado()
        caja.operator_id, caja.operator_since = "u_eva", AHORA
        svc, _ = con_cajas(caja)
        assert svc.register_for(usuario(P.EMPLOYEE, user_id="u_otra"), None) is None

    def test_sin_caja_el_ticket_se_queda_en_la_pantalla(self, con_cajas):
        svc, _ = con_cajas()
        assert svc.register_for(usuario(P.EMPLOYEE), None) is None

    def test_una_caja_de_otro_negocio_no_cuenta_aunque_mande_su_token(self, con_cajas):
        ajena = equipo_autorizado(tenant_id="t_bbb")
        svc, _ = con_cajas(ajena)
        assert svc.register_for(usuario(P.EMPLOYEE, tenant_id="t_aaa"), ajena) is None

    def test_una_caja_desautorizada_ya_no_se_opera(self, con_cajas):
        caja = equipo_autorizado()
        caja.operator_id, caja.operator_since, caja.revoked_at = "u_eva", AHORA, AHORA
        svc, _ = con_cajas(caja)
        assert svc.register_for(usuario(P.EMPLOYEE, user_id="u_eva"), None) is None

    def test_una_caja_vencida_ya_no_se_opera(self, con_cajas):
        from datetime import timedelta
        caja = equipo_autorizado(ttl=timedelta(seconds=-5))
        caja.operator_id, caja.operator_since = "u_eva", AHORA
        svc, _ = con_cajas(caja)
        assert svc.register_for(usuario(P.EMPLOYEE, user_id="u_eva"), None) is None


# ═══════════════════════════════════════════════════════════════════
# Entrar a una caja la hace tuya
# ═══════════════════════════════════════════════════════════════════


class TestOperadorDeLaCaja:
    def _caja(self, servicio, users=None):
        dev = equipo_autorizado()
        svc = servicio(
            users=users or [fila("u_eva", P.EMPLOYEE, code="0004"), fila("u_luis", P.EMPLOYEE, code="0005")],
            devices=[dev],
        )
        return svc, dev

    def _entrar_por_qr(self, svc, dev, user_id):
        c = svc.create_unlock_challenge(dev, HASH, None, "1.2.3.4")
        aprobar(svc, c, usuario(P.EMPLOYEE, user_id=user_id), code=None)
        return svc.claim(c.challenge_id, SECRETO)

    def _entrar_con_datos(self, svc, dev, identificador):
        return svc.login_on_device(dev, DeviceLoginRequest(identifier=identificador, password="Segura123"), "1.2.3.4")

    def test_entrar_por_el_qr_te_hace_el_operador(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_eva")
        assert dev.operator_id == "u_eva" and dev.operator_since is not None

    def test_entrar_con_tus_datos_en_la_computadora_te_hace_el_operador(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_con_datos(svc, dev, "0004")
        assert dev.operator_id == "u_eva"

    def test_el_operador_es_quien_aprobo_no_quien_reclama(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_luis")
        assert dev.operator_id == "u_luis"

    def test_cuando_entra_otra_persona_el_ticket_se_vacia(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_eva")
        FakeTicketService.cleared.clear()

        self._entrar_con_datos(svc, dev, "0005")
        assert dev.operator_id == "u_luis"
        assert FakeTicketService.cleared == [("t_aaa", "dv_1")]

    def test_la_primera_persona_en_una_caja_sin_operador_tambien_vacia_lo_que_hubiera(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_eva")
        assert FakeTicketService.cleared == [("t_aaa", "dv_1")]

    def test_si_la_misma_persona_vuelve_a_entrar_no_pierde_su_ticket(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_eva")
        FakeTicketService.cleared.clear()

        self._entrar_con_datos(svc, dev, "0004")
        assert FakeTicketService.cleared == []

    def test_una_persona_opera_una_sola_caja_a_la_vez(self, servicio):
        otra = equipo_autorizado(device_id="dv_2", token="otro-token")
        svc = servicio(users=[fila("u_eva", P.EMPLOYEE, code="0004")], devices=[equipo_autorizado(), otra])
        primera = svc.devices.devices[0]
        self._entrar_con_datos(svc, primera, "0004")
        self._entrar_con_datos(svc, otra, "0004")
        assert otra.operator_id == "u_eva" and primera.operator_id is None

    def test_un_intento_con_la_contrasena_mala_no_cambia_de_operador(self, servicio):
        from core.exceptions import InvalidCredentialsError
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_eva")
        with pytest.raises(InvalidCredentialsError):
            svc.login_on_device(dev, DeviceLoginRequest(identifier="0005", password="mala"), "1.2.3.4")
        assert dev.operator_id == "u_eva"

    def test_cerrar_sesion_en_la_caja_la_deja_sin_operador_y_sin_ticket(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_eva")
        FakeTicketService.cleared.clear()

        svc.release_register(dev, usuario(P.EMPLOYEE, user_id="u_eva"))
        assert dev.operator_id is None
        assert FakeTicketService.cleared == [("t_aaa", "dv_1")]

    def test_otra_persona_no_puede_soltarle_la_caja_a_quien_esta_cobrando(self, servicio):
        svc, dev = self._caja(servicio)
        self._entrar_por_qr(svc, dev, "u_eva")
        FakeTicketService.cleared.clear()

        svc.release_register(dev, usuario(P.EMPLOYEE, user_id="u_luis"))
        assert dev.operator_id == "u_eva" and FakeTicketService.cleared == []


# ═══════════════════════════════════════════════════════════════════
# Lo que ve el cliente HTTP
# ═══════════════════════════════════════════════════════════════════


class ServicioFalso:
    """Sustituye al servicio real en las rutas: lo que se prueba aquí es el contrato HTTP."""

    registro = None
    estado = None
    aplicadas = []
    error = None

    def __init__(self, db, redis_client=None):
        pass

    def register_for(self, current_user, device):
        return ServicioFalso.registro

    def current(self, tenant_id, device_id):
        return ServicioFalso.estado

    def apply(self, tenant_id, device_id, op):
        if ServicioFalso.error:
            raise ServicioFalso.error
        ServicioFalso.aplicadas.append(op)
        return {**ServicioFalso.estado, "rev": ServicioFalso.estado["rev"] + 1}


class DbFalsa:
    existe_producto = True

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return (1,) if DbFalsa.existe_producto else None


@pytest.fixture
def api(monkeypatch):
    ServicioFalso.registro = SimpleNamespace(id="dv_1", tenant_id="t_aaa", name="Caja 1")
    ServicioFalso.estado = {"uid": "tk_estable_0000000001", "rev": 4, "lines": [{"product_id": "p_1", "quantity": 2}], "ops": ["x"]}
    ServicioFalso.aplicadas, ServicioFalso.error = [], None
    DbFalsa.existe_producto = True
    monkeypatch.setattr(tickets_routes, "TicketService", ServicioFalso)
    app.dependency_overrides[get_current_user] = lambda: usuario(P.EMPLOYEE)
    app.dependency_overrides[get_db] = lambda: DbFalsa()
    app.dependency_overrides[get_optional_device] = lambda: None
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        for dep in (get_current_user, get_db, get_optional_device):
            app.dependency_overrides.pop(dep, None)


OP = {"op_id": "op-0000000000000099", "type": "add", "product_id": "p_1"}


class TestConsultarPorHTTP:
    def test_devuelve_la_caja_y_el_ticket_sin_la_memoria_de_llaves(self, api):
        r = api.get("/api/v1/tickets/current")
        assert r.status_code == 200
        assert r.json() == {
            "register": {"id": "dv_1", "name": "Caja 1"},
            "ticket": {"uid": "tk_estable_0000000001", "rev": 4, "lines": [{"product_id": "p_1", "quantity": 2}]},
        }

    def test_una_sesion_sin_caja_recibe_register_nulo_y_su_ticket_se_queda_local(self, api):
        ServicioFalso.registro = None
        r = api.get("/api/v1/tickets/current")
        assert r.status_code == 200 and r.json() == {"register": None, "ticket": None}

    def test_si_la_pantalla_ya_lo_tiene_la_respuesta_es_204_sin_cuerpo(self, api):
        r = api.get("/api/v1/tickets/current", params={"since": 4, "uid": "tk_estable_0000000001"})
        assert r.status_code == 204 and r.content == b""

    def test_con_una_revision_vieja_se_manda_el_ticket(self, api):
        assert api.get("/api/v1/tickets/current", params={"since": 3, "uid": "tk_estable_0000000001"}).status_code == 200

    def test_con_otro_ticket_se_manda_aunque_la_revision_coincida(self, api):
        """Cobraron y empezaron otro: la revisión puede repetirse, el identificador no."""
        assert api.get("/api/v1/tickets/current", params={"since": 4, "uid": "tk_otro_00000000000001"}).status_code == 200

    def test_since_no_puede_ser_negativo(self, api):
        assert api.get("/api/v1/tickets/current", params={"since": -1}).status_code == 422


class TestCambiarPorHTTP:
    def test_aplica_la_operacion_y_devuelve_el_ticket(self, api):
        r = api.post("/api/v1/tickets/ops", json=OP)
        assert r.status_code == 200
        assert r.json()["ticket"]["rev"] == 5
        assert ServicioFalso.aplicadas[0]["product_id"] == "p_1"

    def test_sin_caja_responde_409_con_su_codigo(self, api):
        ServicioFalso.registro = None
        r = api.post("/api/v1/tickets/ops", json=OP)
        assert r.status_code == 409 and r.json()["code"] == "NO_REGISTER"
        assert ServicioFalso.aplicadas == []

    def test_agregar_un_producto_que_no_es_del_negocio_da_404_y_no_se_guarda(self, api):
        DbFalsa.existe_producto = False
        r = api.post("/api/v1/tickets/ops", json=OP)
        assert r.status_code == 404 and ServicioFalso.aplicadas == []

    def test_quitar_no_consulta_el_catalogo(self, api):
        DbFalsa.existe_producto = False                       # el producto ya no existe, pero quitarlo debe poder
        r = api.post("/api/v1/tickets/ops", json={"op_id": "op-0000000000000098", "type": "remove", "product_id": "p_1"})
        assert r.status_code == 200

    def test_un_ticket_lleno_responde_409(self, api):
        ServicioFalso.error = TicketFullError()
        r = api.post("/api/v1/tickets/ops", json=OP)
        assert r.status_code == 409 and r.json()["code"] == "TICKET_FULL"

    def test_un_ticket_ocupado_responde_503_para_reintentar(self, api):
        ServicioFalso.error = TicketBusyError()
        r = api.post("/api/v1/tickets/ops", json=OP)
        assert r.status_code == 503 and r.json()["code"] == "TICKET_BUSY"

    @pytest.mark.parametrize("cuerpo", [
        {"op_id": "op-0000000000000097", "type": "add"},                                       # sin producto
        {"op_id": "corta", "type": "clear"},                                                   # llave mala
        {"op_id": "op-0000000000000097", "type": "add", "product_id": "p_1", "quantity": 0},
        {"op_id": "op-0000000000000097", "type": "borrar-todo"},
    ])
    def test_una_operacion_mal_formada_es_422(self, api, cuerpo):
        assert api.post("/api/v1/tickets/ops", json=cuerpo).status_code == 422
        assert ServicioFalso.aplicadas == []

    def test_un_empleado_puede_usarlo_es_para_quien_cobra(self, api):
        assert api.post("/api/v1/tickets/ops", json=OP).status_code == 200

    def test_sin_sesion_no_hay_acceso(self, api):
        app.dependency_overrides.pop(get_current_user, None)
        assert api.get("/api/v1/tickets/current").status_code in (401, 403)
        assert api.post("/api/v1/tickets/ops", json=OP).status_code in (401, 403)
