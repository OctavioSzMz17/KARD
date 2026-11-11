"""
La cola que devuelve el inventario de los pedidos cancelados (`orders/stock_returns.py`).

Cancelar deja un trabajo; esta cola lo aplica. Lo que importa:
  · aplicar suma el inventario y deja su movimiento en la bitácora, y marca el trabajo hecho;
  · es IDEMPOTENTE: un trabajo hecho no se vuelve a aplicar, y uno que falla a medias no deja nada
    (ni inventario sumado ni trabajo hecho), así que reintentarlo no duplica;
  · un fallo se reintenta con espera creciente y, agotados los intentos, queda `failed` con su error;
  · un trabajo que falla no detiene a los demás.

La concurrencia de verdad (`FOR UPDATE SKIP LOCKED`, el orden de los bloqueos, la llave única) la
prueba contra Postgres `tests/integracion/e2e_cola_devoluciones.py`: una base falsa no la puede probar.
"""

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from db.models import InventoryTransaction, Product, StockReturn
from orders import stock_returns as cola


# ═══════════════════════════════════════════════════════════════════
# Una "base" mínima que sabe deshacer: lo que no se confirma, no queda
# ═══════════════════════════════════════════════════════════════════


def _valor(condicion):
    return getattr(getattr(condicion, "right", None), "value", None)


class Almacen:
    """El estado que sobrevive entre sesiones: productos, trabajos y la bitácora confirmada."""

    def __init__(self, productos=(), trabajos=()):
        self.productos = {p.id: p for p in productos}
        self.trabajos = list(trabajos)
        self.movimientos = []
        self.sesiones = []
        self.fallar_en_commit = 0       # cuántas devoluciones siguientes fallan al confirmar


class Consulta:
    def __init__(self, sesion, entidad):
        self.s, self.entidad, self.condiciones = sesion, entidad, ()

    def filter(self, *condiciones):
        self.condiciones = condiciones
        return self

    def order_by(self, *a):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        al = self.s.al
        assert self.entidad is StockReturn
        if len(self.condiciones) == 1:                       # por id (anotar un fallo)
            return next((t for t in al.trabajos if t.id == _valor(self.condiciones[0])), None)
        ahora = datetime.now(timezone.utc)                   # tomar el siguiente pendiente que ya toca
        return next((t for t in al.trabajos if t.status == "pending" and t.next_attempt_at <= ahora), None)

    def all(self):
        assert self.entidad is Product
        return list(self.s.al.productos.values())


class Sesion:
    def __init__(self, almacen):
        self.al = almacen
        almacen.sesiones.append(self)
        self.pendientes = []
        self.cerrada = False
        self.commits = self.rollbacks = 0
        # Foto para deshacer: lo que se toca sin confirmar vuelve a como estaba.
        self._productos = {pid: p.inventory_count for pid, p in almacen.productos.items()}
        self._trabajos = copy.deepcopy([vars(t) for t in almacen.trabajos])

    def query(self, entidad):
        return Consulta(self, entidad)

    def add(self, objeto):
        self.pendientes.append(objeto)

    def commit(self):
        # El fallo se inyecta solo al confirmar una devolución (la que suma inventario): anotar el
        # fallo, en su propia sesión, sí puede confirmar.
        if self.al.fallar_en_commit > 0 and any(isinstance(o, InventoryTransaction) for o in self.pendientes):
            self.al.fallar_en_commit -= 1
            raise RuntimeError("se cayó la conexión")
        self.commits += 1
        self.al.movimientos += [o for o in self.pendientes if isinstance(o, InventoryTransaction)]
        self.pendientes = []
        self._productos = {pid: p.inventory_count for pid, p in self.al.productos.items()}
        self._trabajos = copy.deepcopy([vars(t) for t in self.al.trabajos])

    def rollback(self):
        self.rollbacks += 1
        self.pendientes = []
        for pid, cuenta in self._productos.items():
            self.al.productos[pid].inventory_count = cuenta
        for t, foto in zip(self.al.trabajos, self._trabajos):
            vars(t).update(copy.deepcopy(foto))

    def close(self):
        self.cerrada = True


def insumo(pid, stock):
    return SimpleNamespace(id=pid, tenant_id="t_1", inventory_count=stock)


def trabajo(order_id="ord_1", lines=None, **extra):
    t = SimpleNamespace(
        id=f"sr_{order_id}", tenant_id="t_1", order_id=order_id, user_id="u_9", status="pending",
        attempts=0, last_error=None, processed_at=None,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        lines=lines if lines is not None else [{"product_id": "i_maiz", "quantity": 6, "note": "Devolución por cancelar el pedido " + order_id}],
    )
    for k, v in extra.items():
        setattr(t, k, v)
    return t


def correr(almacen, **kw):
    return cola.process_pending(lambda: Sesion(almacen), **kw)


# ═══════════════════════════════════════════════════════════════════
# Aplicar
# ═══════════════════════════════════════════════════════════════════


class TestAplicar:
    def test_suma_el_inventario_y_marca_el_trabajo_hecho(self):
        maiz = insumo("i_maiz", 4)
        al = Almacen([maiz], [trabajo()])

        assert correr(al) == 1

        assert maiz.inventory_count == 10
        assert al.trabajos[0].status == "done" and al.trabajos[0].processed_at is not None

    def test_deja_el_movimiento_en_la_bitacora_a_nombre_de_quien_cancelo(self):
        al = Almacen([insumo("i_maiz", 4)], [trabajo()])
        correr(al)

        [mov] = al.movimientos
        assert (mov.product_id, mov.action, mov.quantity_change) == ("i_maiz", "restock", 6)
        assert mov.quantity_before + mov.quantity_change == mov.quantity_after       # la bitácora cuadra
        assert (mov.user_id, mov.tenant_id) == ("u_9", "t_1")
        assert "ord_1" in mov.notes

    def test_varias_lineas_del_mismo_insumo_encadenan_antes_y_despues(self):
        al = Almacen([insumo("i_maiz", 0)], [trabajo(lines=[
            {"product_id": "i_maiz", "quantity": 2, "note": "a"},
            {"product_id": "i_maiz", "quantity": 3, "note": "b"},
        ])])
        correr(al)

        assert [(m.quantity_before, m.quantity_after) for m in al.movimientos] == [(0, 2), (2, 5)]
        assert al.productos["i_maiz"].inventory_count == 5

    def test_un_producto_que_ya_no_existe_se_salta_y_el_trabajo_se_cierra(self):
        al = Almacen([insumo("i_maiz", 0)], [trabajo(lines=[
            {"product_id": "i_fantasma", "quantity": 1, "note": "x"},
            {"product_id": "i_maiz", "quantity": 2, "note": "y"},
        ])])
        correr(al)

        assert al.productos["i_maiz"].inventory_count == 2
        assert al.trabajos[0].status == "done"

    def test_un_trabajo_sin_lineas_se_cierra_sin_tocar_nada(self):
        al = Almacen([insumo("i_maiz", 7)], [trabajo(lines=[])])
        correr(al)
        assert al.trabajos[0].status == "done" and al.movimientos == []

    def test_sin_trabajos_no_hace_nada(self):
        assert correr(Almacen([insumo("i_maiz", 1)])) == 0


# ═══════════════════════════════════════════════════════════════════
# Idempotencia
# ═══════════════════════════════════════════════════════════════════


class TestIdempotencia:
    def test_un_trabajo_hecho_no_se_vuelve_a_aplicar(self):
        maiz = insumo("i_maiz", 4)
        al = Almacen([maiz], [trabajo()])

        correr(al)
        correr(al)
        correr(al)

        assert maiz.inventory_count == 10                    # 4 + 6, una sola vez
        assert len(al.movimientos) == 1

    def test_un_fallo_a_medias_no_deja_nada_y_el_reintento_devuelve_una_sola_vez(self):
        """
        Se suma el inventario y la conexión se cae al confirmar: todo se deshace, el trabajo sigue
        pendiente, y el reintento devuelve lo mismo UNA vez (no 6 + 6).
        """
        maiz = insumo("i_maiz", 4)
        al = Almacen([maiz], [trabajo()])
        al.fallar_en_commit = 1

        correr(al)
        assert maiz.inventory_count == 4                     # deshecho
        assert al.trabajos[0].status == "pending" and al.movimientos == []

        al.trabajos[0].next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)   # ya toca
        correr(al)
        assert maiz.inventory_count == 10                    # 4 + 6, no 16
        assert al.trabajos[0].status == "done" and len(al.movimientos) == 1

    def test_cada_pedido_devuelve_lo_suyo_sin_mezclarse(self):
        maiz = insumo("i_maiz", 0)
        al = Almacen([maiz], [
            trabajo("ord_1", lines=[{"product_id": "i_maiz", "quantity": 2, "note": "a"}]),
            trabajo("ord_2", lines=[{"product_id": "i_maiz", "quantity": 5, "note": "b"}]),
        ])
        assert correr(al) == 2
        assert maiz.inventory_count == 7
        assert [t.status for t in al.trabajos] == ["done", "done"]


# ═══════════════════════════════════════════════════════════════════
# Fallos y reintentos
# ═══════════════════════════════════════════════════════════════════


class TestReintentos:
    def test_la_espera_crece_y_tiene_tope(self):
        assert [cola.backoff_seconds(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
        assert cola.backoff_seconds(50) == cola.MAX_BACKOFF_SECONDS

    def test_un_fallo_anota_el_error_y_programa_el_siguiente_intento(self):
        al = Almacen([insumo("i_maiz", 4)], [trabajo()])
        al.fallar_en_commit = 1

        antes = datetime.now(timezone.utc)
        correr(al)

        t = al.trabajos[0]
        assert t.status == "pending" and t.attempts == 1
        assert "se cayó la conexión" in t.last_error
        assert t.next_attempt_at >= antes + timedelta(seconds=cola.backoff_seconds(1)) - timedelta(seconds=1)

    def test_no_se_reintenta_antes_de_que_toque(self):
        maiz = insumo("i_maiz", 4)
        al = Almacen([maiz], [trabajo(next_attempt_at=datetime.now(timezone.utc) + timedelta(minutes=5))])
        assert correr(al) == 0 and maiz.inventory_count == 4

    def test_agotados_los_intentos_queda_failed_con_su_error(self):
        maiz = insumo("i_maiz", 4)
        al = Almacen([maiz], [trabajo(attempts=cola.MAX_ATTEMPTS - 1)])
        al.fallar_en_commit = 1

        correr(al)

        t = al.trabajos[0]
        assert t.status == "failed" and t.attempts == cola.MAX_ATTEMPTS
        assert "se cayó la conexión" in t.last_error
        assert maiz.inventory_count == 4

        correr(al)                                           # un failed no se vuelve a tomar
        assert maiz.inventory_count == 4

    def test_un_trabajo_que_falla_no_detiene_a_los_demas(self):
        maiz = insumo("i_maiz", 0)
        al = Almacen([maiz], [
            trabajo("ord_1", lines=[{"product_id": "i_maiz", "quantity": 2, "note": "a"}]),
            trabajo("ord_2", lines=[{"product_id": "i_maiz", "quantity": 5, "note": "b"}]),
        ])
        al.fallar_en_commit = 1                              # falla el primero

        assert correr(al) == 1                               # el segundo sí se aplicó

        assert [t.status for t in al.trabajos] == ["pending", "done"]
        assert maiz.inventory_count == 5

    def test_si_todo_falla_se_detiene_en_vez_de_gastar_los_intentos(self):
        al = Almacen([insumo("i_maiz", 0)], [trabajo(f"ord_{n}") for n in range(10)])
        al.fallar_en_commit = 999

        correr(al)

        # Falló y reprogramó los 3 primeros; los demás ni se tocaron.
        assert [t.attempts for t in al.trabajos] == [1, 1, 1] + [0] * 7

    def test_las_sesiones_se_cierran_siempre(self):
        al = Almacen([insumo("i_maiz", 4)], [trabajo()])
        al.fallar_en_commit = 1
        correr(al)
        assert al.sesiones and all(s.cerrada for s in al.sesiones)


# ═══════════════════════════════════════════════════════════════════
# Encolar y la ruta
# ═══════════════════════════════════════════════════════════════════


class TestEncolar:
    def test_encolar_no_confirma_nada_va_en_la_transaccion_de_quien_cancela(self):
        agregados = []
        db = SimpleNamespace(add=agregados.append, commit=lambda: pytest.fail("encolar no debe confirmar"))

        job = cola.enqueue(db, tenant_id="t_1", order_id="ord_1", lines=[{"product_id": "i_maiz", "quantity": 2, "note": "n"}], user_id="u_9")

        assert agregados == [job]
        assert (job.order_id, job.status, job.attempts, job.user_id) == ("ord_1", "pending", 0, "u_9")
        assert job.id.startswith("sr_")

    def test_la_llave_de_idempotencia_es_unica_por_pedido_en_el_modelo_y_en_la_migracion(self):
        assert StockReturn.__table__.c.order_id.unique is True
        import pathlib
        sql = pathlib.Path(__file__).resolve().parents[1].joinpath("migrations", "014_cola_de_devoluciones.sql").read_text(encoding="utf-8")
        assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_returns_order ON stock_returns (order_id)" in sql


class TestLaRutaDisparaLaCola:
    def _llamar(self, monkeypatch, respuesta):
        from orders import routes

        class _Servicio:
            def __init__(self, db): pass
            def update_sale_status(self, *a, **k): return respuesta

        monkeypatch.setattr(routes, "OrderService", _Servicio)
        tareas = BackgroundTasks()
        resultado = routes.update_sale_status(
            "ord_1", SimpleNamespace(status="canceled", fulfillment_status=None), tareas,
            SimpleNamespace(tenant_id="t_1", user_id="u_9"), db=None,
        )
        return resultado, tareas

    def test_cancelar_aplica_la_cola_despues_de_responder(self, monkeypatch):
        resp = SimpleNamespace(status="canceled", fulfillment_status="canceled")
        resultado, tareas = self._llamar(monkeypatch, resp)
        assert resultado is resp
        assert [t.func for t in tareas.tasks] == [cola.process_pending]

    def test_mover_sin_cancelar_no_toca_la_cola(self, monkeypatch):
        _, tareas = self._llamar(monkeypatch, SimpleNamespace(status="paid", fulfillment_status="ready"))
        assert tareas.tasks == []
