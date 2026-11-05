"""Acceso a datos de equipos autorizados y desafíos de QR (patrón repositorio)."""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from db.models import AuthChallenge, Device, User


class DeviceRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, device: Device) -> Device:
        self.db.add(device)
        self.db.flush()
        return device

    def get_valid_by_token_hash(self, token_hash: str, now: datetime) -> Optional[Device]:
        """El equipo con ese token, solo si sigue autorizado: no revocado y sin vencer."""
        return (
            self.db.query(Device)
            .filter(Device.token_hash == token_hash, Device.revoked_at.is_(None), Device.expires_at > now)
            .first()
        )

    def get_by_id(self, tenant_id: str, device_id: str) -> Optional[Device]:
        return self.db.query(Device).filter(Device.tenant_id == tenant_id, Device.id == device_id).first()

    def list_active(self, tenant_id: str, now: datetime) -> list[tuple[Device, Optional[str]]]:
        """Equipos vigentes del negocio, con el correo de quien los autorizó."""
        rows = (
            self.db.query(Device, User.email)
            .outerjoin(User, User.id == Device.authorized_by)
            .filter(Device.tenant_id == tenant_id, Device.revoked_at.is_(None), Device.expires_at > now)
            .order_by(Device.created_at.desc())
            .all()
        )
        return [(d, email) for d, email in rows]

    def revoke(self, device: Device, now: datetime) -> None:
        device.revoked_at = now
        self.db.add(device)
        self.db.flush()

    def revoke_all(self, tenant_id: str, now: datetime) -> int:
        """Desautoriza todos los equipos del negocio (al cerrar la caja)."""
        n = (
            self.db.query(Device)
            .filter(Device.tenant_id == tenant_id, Device.revoked_at.is_(None))
            .update({Device.revoked_at: now}, synchronize_session=False)
        )
        self.db.flush()
        return n

    def operated_by(self, tenant_id: str, user_id: str, now: datetime) -> Optional[Device]:
        """La caja vigente que esta persona opera ahora (la más reciente), si alguna."""
        return (
            self.db.query(Device)
            .filter(
                Device.tenant_id == tenant_id,
                Device.operator_id == user_id,
                Device.revoked_at.is_(None),
                Device.expires_at > now,
            )
            .order_by(Device.operator_since.desc())
            .first()
        )

    def take_register(self, device: Device, user_id: str, now: datetime) -> bool:
        """
        Esta persona pasa a operar la caja. Una persona opera una sola caja a la vez, así que
        se le quita de las demás. Devuelve True si la caja la operaba OTRA persona (o nadie):
        entonces el ticket de la caja debe vaciarse.
        """
        changed_hands = device.operator_id != user_id
        (
            self.db.query(Device)
            .filter(Device.tenant_id == device.tenant_id, Device.operator_id == user_id, Device.id != device.id)
            .update({Device.operator_id: None, Device.operator_since: None}, synchronize_session=False)
        )
        if changed_hands:
            device.operator_id = user_id
            device.operator_since = now
            self.db.add(device)
        self.db.flush()
        return changed_hands

    def release_register(self, device: Device) -> None:
        device.operator_id = None
        device.operator_since = None
        self.db.add(device)
        self.db.flush()

    def touch(self, device: Device, now: datetime, every: timedelta = timedelta(seconds=60)) -> None:
        """Anota la última vez que se vio el equipo, sin escribir en cada petición."""
        if device.last_seen_at is None or now - device.last_seen_at >= every:
            device.last_seen_at = now
            self.db.add(device)
            self.db.flush()


class ChallengeRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, challenge: AuthChallenge) -> AuthChallenge:
        self.db.add(challenge)
        self.db.flush()
        return challenge

    def get(self, challenge_id: str, lock: bool = False) -> Optional[AuthChallenge]:
        """Con `lock`, la fila queda bloqueada hasta el commit: dos peticiones no la procesan a la vez."""
        q = self.db.query(AuthChallenge).filter(AuthChallenge.id == challenge_id)
        return (q.with_for_update() if lock else q).first()

    def save(self, challenge: AuthChallenge) -> None:
        self.db.add(challenge)
        self.db.flush()

    def delete_expired(self, before: datetime) -> int:
        n = self.db.query(AuthChallenge).filter(AuthChallenge.expires_at < before).delete(synchronize_session=False)
        self.db.flush()
        return n
