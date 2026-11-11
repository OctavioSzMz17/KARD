"""
Concurrencia REAL de la cola de devoluciones contra la base (Neon): muchos hilos, cada uno con su
propia conexión, cancelan pedidos a la vez y varios procesos aplican la cola al mismo tiempo.

Lo que una base falsa no puede probar y aquí sí:
  · cancelar muchos pedidos que COMPARTEN insumos (y los piden en orden distinto) no se traba ni
    tumba a ninguno: cancelar ya no bloquea insumos;
  · varios procesos aplicando la cola a la vez devuelven cada pedido UNA vez (`SKIP LOCKED` + el
    estado `done` en la misma transacción) y el inventario queda exacto;
  · cancelar el MISMO pedido desde 10 conexiones a la vez deja un solo trabajo;
  · la base rechaza un segundo trabajo para el mismo pedido (la llave única).

Necesita la migración 014 aplicada. Como la del ticket compartido, no puede correr en una
transacción que se revierte: crea sus propios productos y pedidos (prefijo `e2ecola`), y los borra
al terminar. Al final comprueba que los conteos quedaron iguales.

    python -P tests/integracion/e2e_cola_devoluciones.py
"""
import os
import sys
import threading
from decimal import Decimal

sys.path.append(os.getcwd())

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.database import SessionLocal, engine
from db.models import Order, OrderItem, Product, ProductRecipe, StockReturn
from orders.service import OrderService
from orders.stock_returns import enqueue, process_pending

P = "e2ecola"
PEDIDOS = 24          # la mitad pide (D1, D2) y la otra mitad (D2, D1): el orden que trababa a la base
STOCK_INICIAL = 1000
fallos: list[str] = []


def check(nombre, ok, detalle=""):
    print(("  OK   " if ok else "  FALLA") + f" {nombre}" + (f"  → {detalle}" if (detalle and not ok) else ""))
    if not ok:
        fallos.append(nombre)


def conteo():
    with engine.connect() as c:
        return tuple(int(x) for x in c.execute(text(
            "select (select count(*) from products), (select count(*) from orders), "
            "(select count(*) from order_items), (select count(*) from product_recipes), "
            "(select count(*) from inventory_transactions), (select count(*) from stock_returns)")).one())


def limpiar():
    with engine.begin() as c:
        c.execute(text(f"delete from stock_returns where order_id like '{P}%'"))
        c.execute(text(f"delete from order_items where order_id like '{P}%'"))
        c.execute(text(f"delete from orders where id like '{P}%'"))
        c.execute(text(f"delete from inventory_transactions where product_id like '{P}%'"))
        c.execute(text(f"delete from product_recipes where id like '{P}%'"))
        c.execute(text(f"delete from products where id like '{P}%'"))


def preparar(tenant_id: str, pedidos: int):
    """Tres insumos compartidos y dos platillos que los usan; `pedidos` pedidos en marcha."""
    with Session(engine) as s:
        # Todo pedido necesita autor (consumer o user): se capturan a nombre de alguien del negocio.
        autor = s.execute(text("select id from users where tenant_id = :t limit 1"), {"t": tenant_id}).scalar()
        assert autor, "el negocio de demostración no tiene usuarios"
        for pid in ("A", "B", "C"):
            s.add(Product(id=f"{P}_i{pid}", tenant_id=tenant_id, product_type="insumo", name=f"e2e insumo {pid}",
                          price=0, inventory_count=STOCK_INICIAL, metadata_={"unidad_medida": "kg"}))
        for pid in ("D1", "D2"):
            s.add(Product(id=f"{P}_{pid}", tenant_id=tenant_id, product_type="final", name=f"e2e platillo {pid}",
                          price=Decimal("50.00"), inventory_count=0, metadata_={}))
        s.flush()
        # D1 gasta A,B,C · D2 gasta C,B,A (en este orden se bloquearían unos a otros)
        for n, (plato, insumo, cant) in enumerate([
            ("D1", "A", 1), ("D1", "B", 2), ("D1", "C", 3), ("D2", "C", 1), ("D2", "B", 2), ("D2", "A", 3),
        ]):
            s.add(ProductRecipe(id=f"{P}_r{n}", tenant_id=tenant_id, final_product_id=f"{P}_{plato}",
                                insumo_product_id=f"{P}_i{insumo}", quantity=cant))
        for n in range(pedidos):
            orden = ("D1", "D2") if n % 2 == 0 else ("D2", "D1")
            s.add(Order(id=f"{P}_o{n}", tenant_id=tenant_id, status="pending", fulfillment_status="preparing",
                        captured_by="staff", user_id=autor, fulfillment_type="pickup", subtotal=Decimal("100.00"), total=Decimal("100.00"),
                        customer_name="e2e"))
            s.flush()
            for k, plato in enumerate(orden):
                s.add(OrderItem(id=f"{P}_o{n}_{k}", order_id=f"{P}_o{n}", product_id=f"{P}_{plato}",
                                product_name=plato, quantity=1, unit_price=Decimal("50.00"), subtotal=Decimal("50.00")))
        s.commit()


def stock():
    with Session(engine) as s:
        return {p.id[-1]: p.inventory_count for p in s.query(Product).filter(Product.id.like(f"{P}_i%"))}


def en_paralelo(n, tarea):
    """Lanza `n` hilos a la vez (arrancan todos juntos) y devuelve los errores que dieron."""
    errores, arranque = [], threading.Barrier(n)

    def corre(i):
        try:
            arranque.wait()
            tarea(i)
        except Exception as e:  # noqa: BLE001 - se informa al final
            errores.append(f"{type(e).__name__}: {str(e)[:160]}")

    hilos = [threading.Thread(target=corre, args=(i,)) for i in range(n)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return errores


def cancelar(tenant_id, order_id):
    db = SessionLocal()
    try:
        OrderService(db).update_sale_status(tenant_id, order_id, "canceled", "canceled", user_id=None)
    finally:
        db.close()


def main() -> int:
    antes = conteo()
    print(f"Estado inicial (productos, pedidos, partidas, recetas, movimientos, devoluciones): {antes}")

    with engine.connect() as c:
        tenant_id = c.execute(text("select id from tenants where business_name ilike '%sabor%' limit 1")).scalar()
        tiene_tabla = c.execute(text("select to_regclass('public.stock_returns')")).scalar()
    assert tenant_id, "no encuentro el negocio de demostración"
    assert tiene_tabla, "falta la migración 014_cola_de_devoluciones.sql"

    limpiar()
    try:
        # ── 1. Muchas cancelaciones a la vez sobre insumos compartidos ────────────────────────────
        print(f"\n1) {PEDIDOS} cancelaciones simultáneas sobre los mismos insumos, pedidos en orden distinto")
        preparar(tenant_id, PEDIDOS)
        errores = en_paralelo(PEDIDOS, lambda i: cancelar(tenant_id, f"{P}_o{i}"))
        check("ninguna cancelación falló ni se trabó", not errores, "; ".join(errores)[:300])

        with Session(engine) as s:
            trabajos = s.query(StockReturn).filter(StockReturn.order_id.like(f"{P}%")).all()
            cancelados = s.query(Order).filter(Order.id.like(f"{P}%"), Order.status == "canceled").count()
        check(f"los {PEDIDOS} pedidos quedaron cancelados", cancelados == PEDIDOS, str(cancelados))
        check(f"quedó un trabajo por pedido ({PEDIDOS})", len(trabajos) == PEDIDOS and {t.status for t in trabajos} == {"pending"},
              f"{len(trabajos)} trabajos, estados {sorted({t.status for t in trabajos})}")
        check("cancelar NO movió el inventario (lo hace la cola)", stock() == {"A": STOCK_INICIAL, "B": STOCK_INICIAL, "C": STOCK_INICIAL}, str(stock()))

        # ── 2. Varios procesos aplicando la cola a la vez ─────────────────────────────────────────
        print("\n2) 6 procesos aplicando la cola al mismo tiempo")
        aplicadas = []
        errores = en_paralelo(6, lambda i: aplicadas.append(process_pending()))
        check("ningún proceso falló", not errores, "; ".join(errores)[:300])
        check(f"entre todos aplicaron exactamente {PEDIDOS}", sum(aplicadas) == PEDIDOS, f"{sum(aplicadas)} ({aplicadas})")

        # Por pedido: D1 devuelve A1 B2 C3 y D2 devuelve C1 B2 A3 → 4 de A, 4 de B, 4 de C
        esperado = {k: STOCK_INICIAL + 4 * PEDIDOS for k in "ABC"}
        check("el inventario quedó exacto: cada pedido devolvió una sola vez", stock() == esperado, f"{stock()} ≠ {esperado}")
        with Session(engine) as s:
            movs = s.execute(text(f"select count(*) from inventory_transactions where product_id like '{P}_i%' and action = 'restock'")).scalar()
            hechos = s.query(StockReturn).filter(StockReturn.order_id.like(f"{P}%"), StockReturn.status == "done").count()
        check(f"{PEDIDOS * 6} movimientos en la bitácora (6 líneas por pedido)", movs == PEDIDOS * 6, str(movs))
        check("todos los trabajos quedaron hechos", hechos == PEDIDOS, str(hechos))

        # ── 3. Volver a aplicar no suma nada ─────────────────────────────────────────────────────
        print("\n3) Volver a correr la cola")
        otra_vez = process_pending()
        check("no había nada pendiente", otra_vez == 0, str(otra_vez))
        check("el inventario no cambió", stock() == esperado, str(stock()))

        # ── 4. El mismo pedido cancelado desde 10 conexiones a la vez ────────────────────────────
        print("\n4) El MISMO pedido cancelado desde 10 conexiones a la vez")
        limpiar()
        preparar(tenant_id, 1)
        errores = en_paralelo(10, lambda i: cancelar(tenant_id, f"{P}_o0"))
        check("ninguna petición falló", not errores, "; ".join(errores)[:300])
        with Session(engine) as s:
            n_trabajos = s.query(StockReturn).filter(StockReturn.order_id == f"{P}_o0").count()
        check("un solo trabajo, no diez", n_trabajos == 1, str(n_trabajos))
        errores = en_paralelo(4, lambda i: process_pending())
        check("ningún proceso falló", not errores, "; ".join(errores)[:300])
        check("se devolvió UNA vez (A+4, B+4, C+4)", stock() == {k: STOCK_INICIAL + 4 for k in "ABC"}, str(stock()))

        # ── 5. La llave única de la base ─────────────────────────────────────────────────────────
        print("\n5) La base rechaza un segundo trabajo para el mismo pedido")
        rechazado = False
        with Session(engine) as s:
            try:
                enqueue(s, tenant_id=tenant_id, order_id=f"{P}_o0", lines=[], user_id=None)
                s.commit()
            except IntegrityError:
                rechazado = True
                s.rollback()
        check("IntegrityError por la llave única (order_id)", rechazado)
    finally:
        limpiar()

    despues = conteo()
    print()
    check(f"la base quedó igual ({antes} → {despues})", antes == despues)

    print("\nTODO BIEN" if not fallos else f"\n{len(fallos)} FALLOS: " + ", ".join(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
