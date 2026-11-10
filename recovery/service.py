"""
Recuperar la contraseña por correo.

Qué cuida, y por qué:

  · No revela si un correo existe. Pedir el enlace responde siempre lo mismo; si la cuenta no existe
    (o está dada de baja) simplemente no se manda nada. El envío lo hace la ruta DESPUÉS de responder,
    así tampoco el tiempo de respuesta delata cuáles correos están registrados.
  · El enlace es aleatorio (256 bits), dura una hora y sirve UNA vez. Solo se guarda su huella: una
    filtración de la base no entrega enlaces utilizables.
  · Pedir uno nuevo anula los anteriores; solo vale el último.
  · Hay tope de solicitudes por correo y por dirección, para que nadie use esto para llenar de
    correos a una persona.
  · Restablecer cierra las sesiones anteriores (`password_changed_at`) y quita el bloqueo por
    intentos fallidos: quien se había quedado fuera de su cuenta puede entrar.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from audit_logging.audit import log_auth_event
from core.config import settings
from core.exceptions import InvalidResetTokenError
from core.redis_client import get_redis
from core.security import hash_password
from db.models import PasswordReset
from recovery.repository import RecoveryRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.recovery")

#: Cuánto vive un enlace.
RESET_TTL = timedelta(hours=1)

#: Solicitudes permitidas por hora: por correo y por dirección de internet.
MAX_PER_EMAIL = 3
MAX_PER_IP = 20
WINDOW_SECONDS = 3600


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class ResetEmail:
    """El correo que hay que mandar. Lo manda la ruta, ya con la respuesta enviada."""

    to: str
    name: Optional[str]
    link: str


@dataclass(frozen=True)
class ResetDone:
    """A quién avisarle que su contraseña cambió."""

    to: str
    name: Optional[str]


def reset_link(token: str) -> str:
    """
    El enlace del correo. El token va en el fragmento (`#`), que el navegador no manda al servidor: no
    queda en los registros del servidor ni viaja en el encabezado `Referer` a otro sitio.
    """
    return f"{settings.APP_URL.rstrip('/')}/restablecer#token={token}"


class PasswordRecoveryService:
    def __init__(self, db: Session, redis_client=None):
        self.db = db
        self.repo = RecoveryRepository(db)
        self.redis = redis_client if redis_client is not None else get_redis()

    # ── Pedir el enlace ───────────────────────────────────────────────
    def request(self, email: str, audience: str, ip: Optional[str]) -> Optional[ResetEmail]:
        """
        Prepara el enlace de esa cuenta, o devuelve None si no hay nada que mandar (no existe, está de
        baja o se pasó del tope). Quien llama responde IGUAL en todos los casos.
        """
        email = email.strip().lower()

        # Los topes se cuentan ANTES de saber si la cuenta existe: así tampoco delatan nada.
        if ip and not self._within_limit(f"auth:forgot:ip:{ip}", MAX_PER_IP):
            logger.warning("Recuperación: tope por dirección alcanzado (%s)", ip)
            return None
        if not self._within_limit(f"auth:forgot:email:{audience}:{email}", MAX_PER_EMAIL):
            logger.warning("Recuperación: tope por correo alcanzado (%s)", email)
            return None

        account = self.repo.find_account(audience, email)
        if account is None or not account.is_active:
            return None

        now = datetime.now(timezone.utc)
        token = secrets.token_urlsafe(32)
        self.repo.invalidate_open(account.kind, account.id, now)
        self.repo.add(PasswordReset(
            id=generate_id("pr", 20),
            account_kind=account.kind,
            account_id=account.id,
            token_hash=sha256(token),
            created_at=now,
            expires_at=now + RESET_TTL,
            requested_ip=ip,
        ))
        self.db.commit()
        log_auth_event(email=email, success=True, ip_address=ip, reason="password_reset_requested")

        link = reset_link(token)
        if not settings.RESEND_API_KEY and settings.ENVIRONMENT == "development":
            # Sin proveedor de correo, en desarrollo el enlace se ve en la consola. Nunca en producción.
            logger.info("Enlace de recuperación (solo desarrollo) para %s: %s", email, link)
        return ResetEmail(to=account.email, name=account.name, link=link)

    # ── Usar el enlace ────────────────────────────────────────────────
    def reset(self, token: str, new_password: str, ip: Optional[str]) -> ResetDone:
        """
        Cambia la contraseña con un enlace válido. Cualquier problema (no existe, caducó, ya se usó, la
        cuenta se dio de baja) es el mismo error: no se le dice a quien adivina cuál fue.
        """
        now = datetime.now(timezone.utc)
        reset = self.repo.get_by_hash(sha256(token))
        if reset is None or reset.used_at is not None or reset.expires_at <= now:
            raise InvalidResetTokenError()

        account = self.repo.get_account(reset.account_kind, reset.account_id)
        if account is None or not account.is_active:
            raise InvalidResetTokenError()

        if not self.repo.consume(reset.id, now):
            raise InvalidResetTokenError()          # otra petición lo gastó un instante antes

        self.repo.set_password(account.kind, account.id, hash_password(new_password), now)
        self.repo.invalidate_open(account.kind, account.id, now)
        self.db.commit()

        self._clear_lockout(account.email)
        log_auth_event(email=account.email, success=True, ip_address=ip, reason="password_reset")
        return ResetDone(to=account.email, name=account.name)

    # ── Internos ──────────────────────────────────────────────────────
    def _within_limit(self, key: str, limit: int) -> bool:
        """Cuenta una solicitud y dice si todavía está dentro del tope. Sin Redis, no hay tope (se deja pasar)."""
        if self.redis is None:
            return True
        try:
            count = self.redis.incr(key)
            if count == 1:
                self.redis.expire(key, WINDOW_SECONDS)
            return count <= limit
        except Exception:
            logger.warning("Redis falló al contar solicitudes; se deja pasar", exc_info=True)
            return True

    def _clear_lockout(self, email: str) -> None:
        """Quien se había bloqueado por intentos fallidos puede volver a entrar con su contraseña nueva."""
        if self.redis is None:
            return
        try:
            self.redis.delete(f"auth:failed:{email}", f"auth:lockout:{email}")
        except Exception:
            logger.warning("Redis falló al quitar el bloqueo de %s", email, exc_info=True)
