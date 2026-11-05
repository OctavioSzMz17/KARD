"""
Servicio del ticket compartido: qué caja opera una sesión, y leer o cambiar el
ticket de esa caja.

Redis guarda el ticket vivo y la base una copia (y, sin Redis, es el único
almacén). Si Redis falla a media operación se sigue con la base: un ticket que
no se pueda guardar sería peor que uno un poco más lento.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import redis
from sqlalchemy.orm import Session

from core.dependencies import CurrentUser
from core.redis_client import get_redis
from db.models import Device
from devices.repository import DeviceRepository
from tickets.logic import State, apply_op, placeholder_state
from tickets.repository import PgDraftStore, RedisDraftStore

logger = logging.getLogger("kard.tickets")


class TicketService:
    def __init__(self, db: Session, redis_client: Optional[redis.Redis] = None):
        self.db = db
        self.pg = PgDraftStore(db)
        client = redis_client if redis_client is not None else get_redis()
        self.redis = RedisDraftStore(client) if client is not None else None

    # ── Qué caja ─────────────────────────────────────────────────────
    def register_for(self, current_user: CurrentUser, device: Optional[Device]) -> Optional[Device]:
        """
        La caja cuyo ticket ve esta sesión.

          · La computadora de la caja manda su token de equipo: ese navegador ES la caja.
          · El celular no lo tiene: su caja es la que esa persona opera (entró a ella por el
            QR de desbloqueo o con sus datos), así no hace falta ningún QR extra.

        Si no hay ninguna, el ticket se queda en la pantalla de quien lo arma.
        """
        if device is not None and device.tenant_id == current_user.tenant_id:
            return device
        return DeviceRepository(self.db).operated_by(current_user.tenant_id, current_user.user_id, datetime.now(timezone.utc))

    # ── Leer ─────────────────────────────────────────────────────────
    def current(self, tenant_id: str, device_id: str) -> State:
        state = self._load(tenant_id, device_id)
        return state if state is not None else placeholder_state(device_id)

    def _load(self, tenant_id: str, device_id: str) -> Optional[State]:
        if self.redis is not None:
            try:
                state = self.redis.load(tenant_id, device_id)
                if state is not None:
                    return state
            except redis.RedisError:
                logger.warning("Redis no respondió al leer el ticket; se usa la base", exc_info=True)
        state = self.pg.load(tenant_id, device_id)
        if state is not None and self.redis is not None:
            try:
                self.redis.restore(tenant_id, device_id, state)
            except redis.RedisError:
                logger.warning("No se pudo volver a cargar el ticket en Redis", exc_info=True)
        return state

    # ── Cambiar ──────────────────────────────────────────────────────
    def apply(self, tenant_id: str, device_id: str, op: dict) -> State:
        """Aplica una operación y devuelve el estado completo resultante."""

        def change(state: State) -> tuple[State, bool]:
            return apply_op(state, op)

        if self.redis is not None:
            try:
                new, changed = self.redis.update(tenant_id, device_id, lambda: self.pg.load(tenant_id, device_id), change)
            except redis.RedisError:
                logger.warning("Redis falló al guardar el ticket; se sigue con la base", exc_info=True)
            else:
                if changed:
                    self.pg.save_snapshot(tenant_id, device_id, new)
                self.db.commit()
                return new

        new, _ = self.pg.update(tenant_id, device_id, change)
        self.db.commit()
        return new

    # ── Soltar ───────────────────────────────────────────────────────
    def clear(self, tenant_id: str, device_id: str) -> None:
        """Borra el ticket de una caja (entró otra persona, o la caja se cerró). No hace commit."""
        if self.redis is not None:
            try:
                self.redis.delete(tenant_id, device_id)
            except redis.RedisError:
                logger.warning("No se pudo borrar el ticket de Redis", exc_info=True)
        self.pg.delete(tenant_id, device_id)

    def clear_tenant(self, tenant_id: str) -> None:
        """Borra los tickets de todas las cajas del negocio (al cerrar la caja). No hace commit."""
        if self.redis is not None:
            try:
                self.redis.delete_tenant(tenant_id)
            except redis.RedisError:
                logger.warning("No se pudieron borrar los tickets de Redis", exc_info=True)
        self.pg.delete_tenant(tenant_id)
