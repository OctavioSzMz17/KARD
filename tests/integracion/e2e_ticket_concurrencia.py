"""
Concurrencia REAL del ticket compartido contra la base (Neon): varios hilos, cada uno con su
propia conexión, cambian el MISMO ticket a la vez. Ninguna operación debe perderse.

A diferencia de los demás recorridos, esta prueba NO puede correr dentro de una transacción que
se revierte (necesita confirmar de verdad para que las conexiones se vean entre sí): crea una
caja temporal, trabaja sobre ella y la borra al terminar (su ticket cae por `ON DELETE CASCADE`).
Al final comprueba que los conteos quedaron iguales.

Es la prueba del candado de la fila (`SELECT ... FOR UPDATE`) del almacén de la base: sin él, dos
peticiones leen el mismo estado, cada una agrega lo suyo y una pisa a la otra.

    python -P tests/integracion/e2e_ticket_concurrencia.py
"""
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

sys.path.append(os.getcwd())

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.database import engine
from db.models import Device
from tickets.logic import empty_state
from tickets.repository import PgDraftStore

HILOS = 4
OPERACIONES_POR_HILO = 12
DEVICE_ID = "dv_e2e_concurrencia"


def conteo():
    with engine.connect() as c:
        return tuple(int(x) for x in c.execute(text(
            "select (select count(*) from devices), (select count(*) from ticket_drafts)")).one())


def main() -> int:
    antes = conteo()
    print(f"Estado inicial (equipos, tickets): {antes}")

    with engine.connect() as c:
        tenant_id = c.execute(text("select id from tenants where business_name ilike '%sabor%' limit 1")).scalar()
    assert tenant_id, "no encuentro el negocio de demostración"

    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.query(Device).filter(Device.id == DEVICE_ID).delete()
        s.add(Device(id=DEVICE_ID, tenant_id=tenant_id, name="e2e concurrencia", token_hash="e2e-" + "0" * 60,
                     expires_at=now + timedelta(hours=1)))
        s.commit()

    errores, fallos = [], []

    def trabajar(n_hilo: int) -> None:
        try:
            for i in range(OPERACIONES_POR_HILO):
                def agregar(state):
                    from tickets.logic import apply_op
                    return apply_op(state, {"op_id": f"conc-{n_hilo}-{i}-0000000000", "type": "add", "product_id": "p_conc"})

                with Session(engine) as s:
                    PgDraftStore(s).update(tenant_id, DEVICE_ID, agregar)
                    s.commit()
        except Exception as e:  # noqa: BLE001 - se informa al final
            errores.append(repr(e))

    try:
        hilos = [threading.Thread(target=trabajar, args=(n,)) for n in range(HILOS)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        with Session(engine) as s:
            estado = PgDraftStore(s).load(tenant_id, DEVICE_ID)
        total = HILOS * OPERACIONES_POR_HILO
        cantidad = estado["lines"][0]["quantity"] if estado and estado["lines"] else 0

        def check(nombre, ok, detalle=""):
            print(("  OK   " if ok else "  FALLA") + f" {nombre}" + (f"  → {detalle}" if (detalle and not ok) else ""))
            if not ok:
                fallos.append(nombre)

        print(f"\n{HILOS} conexiones × {OPERACIONES_POR_HILO} operaciones sobre el MISMO ticket")
        check("ningún hilo falló", not errores, "; ".join(errores)[:200])
        check(f"no se perdió ninguna operación: {total} unidades", cantidad == total, f"hay {cantidad} de {total}")
        check(f"la revisión subió exactamente {total} veces", estado is not None and estado["rev"] == total, str(estado and estado["rev"]))
        check("una sola línea, no una por conexión", estado is not None and len(estado["lines"]) == 1)
    finally:
        with Session(engine) as s:
            s.query(Device).filter(Device.id == DEVICE_ID).delete()
            s.commit()

    despues = conteo()
    print()
    print(("  OK   " if antes == despues else "  FALLA") + f" la base quedó igual ({antes} → {despues})")
    if antes != despues:
        fallos.append("la base quedó igual")

    print("\nTODO BIEN" if not fallos else f"\n{len(fallos)} FALLOS: " + ", ".join(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
