"""
Almacenes del ticket compartido de una caja.

  RedisDraftStore  El ticket VIVO. Lectura rápida (las pantallas preguntan cada
                   segundo si hay algo nuevo) y caducidad por inactividad.
  PgDraftStore     Copia en la base: recupera el ticket si Redis se reinicia y
                   es el almacén completo cuando no hay Redis.

Los dos exponen `update(...)`: leer, aplicar una función PURA y guardar, de forma
atómica. La función puede ejecutarse más de una vez si hay un choque con otra
petición, así que no debe tener efectos.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import redis
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.exceptions import TicketBusyError
from db.models import TicketDraft
from tickets.logic import State, empty_state

logger = logging.getLogger("kard.tickets")

#: El ticket caduca tras tantas horas SIN cambios (cada cambio renueva el plazo).
TTL_SECONDS = 12 * 3600

#: Reintentos si otra petición modifica el mismo ticket a la vez.
MAX_RETRIES = 8

UpdateFn = Callable[[State], tuple[State, bool]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RedisDraftStore:
    def __init__(self, client: redis.Redis):
        self.r = client

    @staticmethod
    def key(tenant_id: str, device_id: str) -> str:
        # El negocio va en la clave: una caja de otro negocio no existe para quien pregunta.
        return f"ticket:{tenant_id}:{device_id}"

    def load(self, tenant_id: str, device_id: str) -> Optional[State]:
        raw = self.r.get(self.key(tenant_id, device_id))
        return json.loads(raw) if raw else None

    def restore(self, tenant_id: str, device_id: str, state: State) -> None:
        """Vuelve a cargar un ticket recuperado de la base, solo si Redis no tiene ya otro."""
        self.r.set(self.key(tenant_id, device_id), json.dumps(state, separators=(",", ":")), ex=TTL_SECONDS, nx=True)

    def update(
        self,
        tenant_id: str,
        device_id: str,
        fallback: Callable[[], Optional[State]],
        fn: UpdateFn,
    ) -> tuple[State, bool]:
        """
        Lectura, cambio y guardado atómicos (WATCH/MULTI). Si la clave no existe, parte de
        `fallback()` (la copia de la base) y, si tampoco hay, de un ticket vacío.
        """
        key = self.key(tenant_id, device_id)
        for _ in range(MAX_RETRIES):
            with self.r.pipeline() as pipe:
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    state = json.loads(raw) if raw else (fallback() or empty_state())
                    new, changed = fn(state)
                    if not changed:
                        return state, False
                    pipe.multi()
                    pipe.set(key, json.dumps(new, separators=(",", ":")), ex=TTL_SECONDS)
                    pipe.execute()
                    return new, True
                except redis.WatchError:
                    continue
        raise TicketBusyError()

    def delete(self, tenant_id: str, device_id: str) -> None:
        self.r.delete(self.key(tenant_id, device_id))

    def delete_tenant(self, tenant_id: str) -> None:
        for key in self.r.scan_iter(match=f"ticket:{tenant_id}:*"):
            self.r.delete(key)


class PgDraftStore:
    def __init__(self, db: Session):
        self.db = db

    def load(self, tenant_id: str, device_id: str) -> Optional[State]:
        row = (
            self.db.query(TicketDraft)
            .filter(TicketDraft.device_id == device_id, TicketDraft.tenant_id == tenant_id)
            .first()
        )
        if row is None or row.expires_at <= _now():
            return None
        return row.payload

    def update(self, tenant_id: str, device_id: str, fn: UpdateFn) -> tuple[State, bool]:
        """
        Lectura, cambio y guardado con la fila bloqueada. La fila se crea antes (si no existe)
        para que dos primeras peticiones simultáneas no choquen al insertar.
        """
        now = _now()
        seed = empty_state()
        self.db.execute(
            insert(TicketDraft)
            .values(
                device_id=device_id, tenant_id=tenant_id, uid=seed["uid"], rev=0, payload=seed,
                updated_at=now, expires_at=now + timedelta(seconds=TTL_SECONDS),
            )
            .on_conflict_do_nothing(index_elements=[TicketDraft.device_id])
        )
        row = (
            self.db.query(TicketDraft)
            .filter(TicketDraft.device_id == device_id, TicketDraft.tenant_id == tenant_id)
            .with_for_update()
            .one()
        )
        state = empty_state() if row.expires_at <= now else row.payload
        new, changed = fn(state)
        if not changed:
            return state, False
        row.uid, row.rev, row.payload = new["uid"], new["rev"], new
        row.updated_at = now
        row.expires_at = now + timedelta(seconds=TTL_SECONDS)
        self.db.flush()
        return new, True

    def save_snapshot(self, tenant_id: str, device_id: str, state: State) -> None:
        """Copia del ticket vivo. Solo avanza: una copia atrasada no pisa una más nueva."""
        now = _now()
        stmt = insert(TicketDraft).values(
            device_id=device_id, tenant_id=tenant_id, uid=state["uid"], rev=state["rev"], payload=state,
            updated_at=now, expires_at=now + timedelta(seconds=TTL_SECONDS),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[TicketDraft.device_id],
            set_={
                "uid": stmt.excluded.uid, "rev": stmt.excluded.rev, "payload": stmt.excluded.payload,
                "updated_at": stmt.excluded.updated_at, "expires_at": stmt.excluded.expires_at,
            },
            where=TicketDraft.rev < stmt.excluded.rev,
        )
        self.db.execute(stmt)

    def delete(self, tenant_id: str, device_id: str) -> None:
        self.db.query(TicketDraft).filter(
            TicketDraft.device_id == device_id, TicketDraft.tenant_id == tenant_id
        ).delete(synchronize_session=False)

    def delete_tenant(self, tenant_id: str) -> None:
        self.db.query(TicketDraft).filter(TicketDraft.tenant_id == tenant_id).delete(synchronize_session=False)
