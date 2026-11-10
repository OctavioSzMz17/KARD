"""Acceso a datos de la recuperación de contraseña (patrón repositorio): solo consultas, sin reglas."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from db.models import Consumer, PasswordReset, User


@dataclass(frozen=True)
class Account:
    """Quien pide recuperar su contraseña, sea del negocio o cliente de la tienda."""

    kind: str                      # 'user' | 'consumer'
    id: str
    email: str
    name: Optional[str]
    is_active: bool


def _account(kind: str, row) -> Account:
    name = row.full_name if kind == "user" else row.name
    return Account(kind=kind, id=row.id, email=row.email, name=name, is_active=bool(row.is_active))


class RecoveryRepository:
    def __init__(self, db: Session):
        self.db = db

    # ── Cuentas ───────────────────────────────────────────────────────
    def find_account(self, audience: str, email: str) -> Optional[Account]:
        """La cuenta de ese lado de la plataforma con ese correo (el correo se guarda en minúsculas)."""
        if audience == "cliente":
            row = self.db.query(Consumer).filter(Consumer.email == email).first()
            return _account("consumer", row) if row is not None else None
        row = self.db.query(User).filter(User.email == email).first()
        return _account("user", row) if row is not None else None

    def get_account(self, kind: str, account_id: str) -> Optional[Account]:
        model = Consumer if kind == "consumer" else User
        row = self.db.query(model).filter(model.id == account_id).first()
        return _account(kind, row) if row is not None else None

    def set_password(self, kind: str, account_id: str, password_hash: str, now: datetime) -> None:
        """Guarda la contraseña nueva y anota cuándo: las sesiones anteriores a ese instante dejan de valer."""
        if kind == "consumer":
            stmt = (
                update(Consumer).where(Consumer.id == account_id)
                .values(hashed_password=password_hash, password_changed_at=now)
            )
        else:
            stmt = (
                update(User).where(User.id == account_id)
                .values(password_hash=password_hash, password_changed_at=now)
            )
        self.db.execute(stmt)

    # ── Enlaces ───────────────────────────────────────────────────────
    def add(self, reset: PasswordReset) -> None:
        self.db.add(reset)
        self.db.flush()

    def get_by_hash(self, token_hash: str) -> Optional[PasswordReset]:
        return self.db.query(PasswordReset).filter(PasswordReset.token_hash == token_hash).first()

    def invalidate_open(self, kind: str, account_id: str, now: datetime) -> int:
        """Anula los enlaces que esta cuenta tenía sin usar: solo vale el último que pidió."""
        result = self.db.execute(
            update(PasswordReset)
            .where(
                PasswordReset.account_kind == kind,
                PasswordReset.account_id == account_id,
                PasswordReset.used_at.is_(None),
            )
            .values(used_at=now)
        )
        return result.rowcount or 0

    def consume(self, reset_id: str, now: datetime) -> bool:
        """
        Gasta el enlace. Es atómico: si dos peticiones llegan a la vez con el mismo enlace, solo una
        encuentra `used_at` vacío y la otra recibe False.
        """
        result = self.db.execute(
            update(PasswordReset)
            .where(
                PasswordReset.id == reset_id,
                PasswordReset.used_at.is_(None),
                PasswordReset.expires_at > now,
            )
            .values(used_at=now)
        )
        return (result.rowcount or 0) == 1
