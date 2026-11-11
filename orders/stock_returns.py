"""
Cola de devoluciones de inventario por pedidos cancelados.

Por qué una cola. Devolver el inventario dentro de la petición de cancelar obligaba a bloquear los
insumos del pedido mientras se cancelaba. Dos cancelaciones que comparten un insumo se esperaban
entre sí y, si los tomaban en orden distinto, la base las trababa y tumbaba una (deadlock).

Cómo funciona ahora:

  1. CANCELAR (`enqueue`): la petición marca el pedido y deja UN trabajo en `stock_returns` con lo
     que hay que devolver. No toca ningún insumo, así que cancelar es instantáneo y dos
     cancelaciones nunca se estorban.
  2. APLICAR (`process_pending`): un proceso toma los trabajos de uno en uno, bloquea los insumos
     SIEMPRE en el mismo orden (por id) y suma el inventario. Marcar el trabajo como hecho va en
     la MISMA transacción que la suma: o pasa todo o no pasa nada.

Idempotencia, en tres capas (cada una cubre un fallo distinto):

  · `order_id` es único en la tabla: un pedido no puede tener dos devoluciones, ni por doble clic,
    ni por un reintento, ni por dos servidores a la vez. Es la red final, la pone la base.
  · Un trabajo se toma con `FOR UPDATE SKIP LOCKED`: si dos procesos lo ven a la vez, uno se lo
    queda y el otro pasa al siguiente. Nadie espera a nadie.
  · El estado `done` se guarda junto con el inventario. Un trabajo que falla hace rollback de
    todo, así que reintentarlo no puede devolver dos veces lo que ya sumó a medias.

Si falla (la base se cayó, un deadlock con una venta), el trabajo se reintenta con espera creciente
y, tras `MAX_ATTEMPTS`, queda en `failed` con su último error para revisarlo a mano.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import InventoryTransaction, Product, StockReturn
from utils.helpers import generate_id

logger = logging.getLogger("kard.stock_returns")

MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 600
# Cuántos trabajos atiende una pasada: acota lo que tarda y deja el resto a la siguiente.
BATCH_LIMIT = 50


def backoff_seconds(attempts: int) -> int:
    """Espera antes del siguiente intento: 5 s, 10 s, 20 s… hasta 10 min."""
    return min(BASE_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0)), MAX_BACKOFF_SECONDS)


def enqueue(db: Session, *, tenant_id: str, order_id: str, lines: list[dict], user_id: str | None) -> StockReturn:
    """
    Deja el trabajo de devolver el inventario de un pedido. No hace commit: va en la misma
    transacción que la cancelación, así que el pedido nunca queda cancelado sin su devolución.
    """
    job = StockReturn(
        id=generate_id("sr"),
        tenant_id=tenant_id,
        order_id=order_id,
        lines=lines,
        status="pending",
        attempts=0,
        user_id=user_id,
        created_at=datetime.now(timezone.utc),
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(job)
    return job


def _apply(db: Session, job: StockReturn) -> None:
    """
    Suma al inventario lo que dice el trabajo y lo marca hecho. No hace commit.

    Los insumos se bloquean todos de una vez y ordenados por id: dos trabajos que comparten
    insumos los toman en el mismo orden, así que no pueden trabarse entre sí.
    """
    lines: Iterable[dict] = job.lines or []
    ids = sorted({line["product_id"] for line in lines})

    # Sin filtrar por is_deleted: un producto dado de baja después también recupera lo suyo.
    rows = []
    if ids:
        rows = (
            db.query(Product)
            .filter(Product.id.in_(ids), Product.tenant_id == job.tenant_id)
            .order_by(Product.id)
            .with_for_update()
            .all()
        )
    by_id = {p.id: p for p in rows}

    for line in lines:
        product = by_id.get(line["product_id"])
        if product is None:
            continue                       # el producto ya no existe: no hay a dónde devolver
        quantity = int(line["quantity"])
        before = product.inventory_count
        product.inventory_count = before + quantity
        db.add(InventoryTransaction(
            product_id=product.id,
            tenant_id=job.tenant_id,
            action="restock",
            quantity_change=quantity,
            quantity_before=before,
            quantity_after=product.inventory_count,
            user_id=job.user_id,
            notes=line.get("note"),
        ))

    job.status = "done"
    job.processed_at = datetime.now(timezone.utc)


def _register_failure(session_factory: Callable[[], Session], job_id: str, error: Exception) -> None:
    """Anota el fallo en una sesión propia (la del intento ya hizo rollback) y programa el reintento."""
    db = session_factory()
    try:
        job = db.query(StockReturn).filter(StockReturn.id == job_id).with_for_update().first()
        if job is None or job.status != "pending":
            return
        job.attempts += 1
        job.last_error = f"{type(error).__name__}: {error}"[:500]
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            logger.error(
                "Devolución de inventario AGOTÓ sus reintentos (hay que revisarla a mano): order_id=%s error=%s",
                job.order_id, job.last_error,
            )
        else:
            job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(job.attempts))
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("No se pudo anotar el fallo del trabajo %s", job_id, exc_info=True)
    finally:
        db.close()


def process_one(session_factory: Callable[[], Session]) -> str:
    """
    Atiende UN trabajo pendiente. Devuelve "done", "failed" o "empty" (no había nada que hacer).

    Cada trabajo va en su propia transacción: uno que falla no arrastra a los demás.
    """
    db = session_factory()
    job_id = None
    try:
        job = (
            db.query(StockReturn)
            .filter(StockReturn.status == "pending", StockReturn.next_attempt_at <= func.now())
            .order_by(StockReturn.created_at)
            .with_for_update(skip_locked=True)      # lo que otro proceso ya tomó, se salta
            .first()
        )
        if job is None:
            return "empty"
        job_id = job.id
        _apply(db, job)
        db.commit()
        return "done"
    except Exception as exc:
        db.rollback()
        logger.warning("Falló una devolución de inventario (se reintenta): job=%s", job_id, exc_info=True)
        if job_id is not None:
            _register_failure(session_factory, job_id, exc)
        return "failed"
    finally:
        db.close()


def process_pending(session_factory: Callable[[], Session] | None = None, limit: int = BATCH_LIMIT) -> int:
    """
    Aplica las devoluciones pendientes (hasta `limit`). Devuelve cuántas aplicó.

    Se llama justo después de cancelar (para que el inventario suba al momento) y cada cierto
    tiempo (para los reintentos y lo que un reinicio dejó a medias). Es seguro llamarla desde
    varios procesos a la vez: cada trabajo lo toma uno solo.
    """
    if session_factory is None:
        from db.database import SessionLocal
        session_factory = SessionLocal

    applied = 0
    failures = 0
    for _ in range(limit):
        outcome = process_one(session_factory)
        if outcome == "empty":
            break
        if outcome == "done":
            applied += 1
        else:
            # Un trabajo que falla queda en espera; si fallaran uno tras otro (la base está caída),
            # seguir solo gasta intentos.
            failures += 1
            if failures >= 3:
                break
    if applied:
        logger.info("Devoluciones de inventario aplicadas: %d", applied)
    return applied
