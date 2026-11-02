"""
Equipos autorizados y acceso por QR.

Dos tipos de QR, mismo mecanismo:

  enroll  Una computadora que aún no es de nadie muestra un QR. Alguien de la
          gerencia lo lee con su celular, teclea el código de 4 dígitos que ve
          en la computadora y la autoriza para el día ("Caja 1").
  unlock  Una computadora YA autorizada muestra un QR. Una persona del negocio
          lo lee con su celular, ya con su sesión iniciada, y la computadora
          abre sesión a su nombre.

El diseño parte de que el QR lo puede fotografiar cualquiera:

  * Lleva solo el identificador del desafío (aleatorio, de un solo uso, 2.5 min).
    Ninguna credencial.
  * Quien recibe el resultado es únicamente la computadora que lo pidió: guardó
    un secreto del que el servidor solo conoce el hash. Fotografiar el QR no
    sirve para reclamar nada.
  * Autorizar un equipo exige teclear el código que se ve en SU pantalla: no se
    puede aprobar a ciegas un QR que alguien más te puso enfrente.
  * El token del equipo se entrega una sola vez y en la base solo queda su hash.
"""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from audit_logging.audit import log_auth_event, log_operation
from auth.models import TokenResponse
from auth.repository import TenantRepository, UserRepository
from auth.service import AuthService, LOCKOUT_SECONDS, MAX_FAILED_ATTEMPTS
from core.config import settings
from core.dependencies import CurrentUser
from core.exceptions import (
    AccountLockedError,
    ChallengeExpiredError,
    ChallengeNotFoundError,
    DeviceNotAuthorizedError,
    InvalidCredentialsError,
    PermissionDeniedError,
    TooManyChallengesError,
    WrongVerificationCodeError,
)
from core.permissions import DEVICES_AUTHORIZE, POS_SELL, needs_authorized_device
from core.redis_client import get_redis
from core.security import verify_password
from db.models import AuthChallenge, Device
from devices.models import (
    ApproveRequest,
    ApproveResult,
    ChallengeCreated,
    ChallengeInfo,
    ClaimResponse,
    DeviceInfo,
    DeviceList,
    DeviceLoginRequest,
)
from devices.repository import ChallengeRepository, DeviceRepository
from utils.helpers import generate_id

logger = logging.getLogger("kard.devices")

#: Intentos de teclear el código antes de anular el QR.
MAX_CODE_ATTEMPTS = 3
#: QR que se pueden pedir por dirección IP en 10 minutos (sin sesión, hay que limitar).
MAX_CHALLENGES_PER_IP = 30
DEFAULT_DEVICE_NAME = "Caja"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def describe_user_agent(user_agent: Optional[str]) -> str:
    """'Chrome en Windows': lo bastante claro para reconocer el equipo al aprobar, sin mostrar la cadena cruda."""
    ua = user_agent or ""
    if not ua:
        return "Equipo desconocido"

    if "Edg/" in ua or "EdgA/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "CriOS" in ua or "Chrome/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Navegador"

    if "Android" in ua:
        system = "Android"
    elif "iPhone" in ua or "iPad" in ua or "iPod" in ua:
        system = "iOS"
    elif "Windows" in ua:
        system = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "macOS"
    elif "CrOS" in ua:
        system = "ChromeOS"
    elif "Linux" in ua:
        system = "Linux"
    else:
        system = "sistema desconocido"
    return f"{browser} en {system}"


def clean_device_name(name: Optional[str]) -> str:
    cleaned = " ".join((name or "").split())[:60]
    return cleaned or DEFAULT_DEVICE_NAME


def assert_can_charge(current_user: CurrentUser, device: Optional[Device]) -> None:
    """
    Cobrar desde este equipo: encargados y empleados solo desde uno autorizado
    HOY por la gerencia, y de su mismo negocio. Dueño, gerente y subgerente, desde cualquiera.
    """
    if not needs_authorized_device(current_user.position):
        return
    if device is None or device.tenant_id != current_user.tenant_id:
        raise DeviceNotAuthorizedError("Este equipo no está autorizado hoy para cobrar")


class DeviceService:
    def __init__(self, db: Session):
        self.db = db
        self.devices = DeviceRepository(db)
        self.challenges = ChallengeRepository(db)
        self.users = UserRepository(db)
        self.tenants = TenantRepository(db)

    # ------------------------------------------------------------------
    # Crear el QR (lo pide la computadora)
    # ------------------------------------------------------------------

    def create_enroll_challenge(self, secret_hash: str, user_agent: Optional[str], ip: Optional[str]) -> ChallengeCreated:
        self._throttle(ip)
        return self._create_challenge("enroll", secret_hash, user_agent, ip, tenant_id=None, device_id=None)

    def create_unlock_challenge(self, device: Device, secret_hash: str, user_agent: Optional[str], ip: Optional[str]) -> ChallengeCreated:
        self._throttle(ip)
        return self._create_challenge("unlock", secret_hash, user_agent, ip, tenant_id=device.tenant_id, device_id=device.id)

    def _create_challenge(self, kind, secret_hash, user_agent, ip, tenant_id, device_id) -> ChallengeCreated:
        now = _now()
        # Limpieza oportunista: lo vencido hace más de una hora ya no sirve a nadie.
        self.challenges.delete_expired(now - timedelta(hours=1))

        ttl = settings.CHALLENGE_TTL_SECONDS
        challenge = AuthChallenge(
            id=secrets.token_urlsafe(16),
            kind=kind,
            tenant_id=tenant_id,
            device_id=device_id,
            secret_hash=secret_hash,
            verification_code=f"{secrets.randbelow(10_000):04d}",
            attempts=0,
            user_agent=(user_agent or "")[:300] or None,
            ip_address=(ip or "")[:64] or None,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        self.challenges.create(challenge)
        self.db.commit()
        return ChallengeCreated(
            challenge_id=challenge.id,
            verification_code=challenge.verification_code,
            expires_at=challenge.expires_at,
            expires_in=ttl,
        )

    @staticmethod
    def _throttle(ip: Optional[str]) -> None:
        """Pedir QR no exige sesión: se limita por IP para que nadie llene la tabla."""
        redis_client = get_redis()
        if redis_client is None or not ip:
            return
        try:
            key = f"devauth:create:{ip}"
            n = redis_client.incr(key)
            if n == 1:
                redis_client.expire(key, 600)
            if n > MAX_CHALLENGES_PER_IP:
                raise TooManyChallengesError()
        except TooManyChallengesError:
            raise
        except Exception:
            logger.warning("Redis rate-limit check failed; continuing", exc_info=True)

    # ------------------------------------------------------------------
    # Ver y aprobar (lo hace el celular de una persona con sesión)
    # ------------------------------------------------------------------

    def info(self, current_user: CurrentUser, challenge_id: str) -> ChallengeInfo:
        challenge = self._visible_pending(current_user, challenge_id)
        device_name = None
        if challenge.kind == "unlock" and challenge.device_id:
            device = self.devices.get_by_id(challenge.tenant_id, challenge.device_id)
            device_name = device.name if device else None
        return ChallengeInfo(
            kind=challenge.kind,
            device_label=describe_user_agent(challenge.user_agent),
            ip_address=challenge.ip_address,
            created_at=challenge.created_at,
            expires_at=challenge.expires_at,
            device_name=device_name,
            requires_code=challenge.kind == "enroll",
        )

    def approve(self, current_user: CurrentUser, challenge_id: str, payload: ApproveRequest) -> ApproveResult:
        challenge = self.challenges.get(challenge_id, lock=True)
        self._require_pending(current_user, challenge)

        if challenge.kind == "enroll":
            if not current_user.can(DEVICES_AUTHORIZE):
                raise PermissionDeniedError("Solo la gerencia puede autorizar un equipo")
            self._check_code(challenge, payload.code)

            challenge.device_name = clean_device_name(payload.device_name)
            challenge.tenant_id = current_user.tenant_id
        else:
            if not current_user.can(POS_SELL):
                raise PermissionDeniedError("Tu puesto no puede abrir sesión en una caja")
            device = self.devices.get_by_id(challenge.tenant_id, challenge.device_id)
            if device is None or device.revoked_at is not None or device.expires_at <= _now():
                raise ChallengeNotFoundError(challenge_id)

        challenge.approved_by = current_user.user_id
        challenge.status = "approved"
        self.challenges.save(challenge)
        self.db.commit()

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="APPROVE_ENROLL" if challenge.kind == "enroll" else "APPROVE_UNLOCK",
            resource_id=challenge.id,
            after={"device_name": challenge.device_name, "device_id": challenge.device_id},
        )

        cash_opened = False
        if challenge.kind == "enroll" and payload.open_float is not None:
            cash_opened = self._open_cash(current_user, payload.open_float)
        return ApproveResult(status="approved", cash_opened=cash_opened)

    def _open_cash(self, current_user: CurrentUser, opening_float) -> bool:
        """Abre la caja como parte de autorizar el equipo; si ya había una abierta, la deja como está."""
        from cash.models import OpenCashRequest
        from cash.service import CashService
        from core.exceptions import CashSessionAlreadyOpenError

        try:
            CashService(self.db).open(current_user, OpenCashRequest(opening_float=opening_float))
            return True
        except CashSessionAlreadyOpenError:
            return False

    def _check_code(self, challenge: AuthChallenge, code: Optional[str]) -> None:
        """El código se compara sin dar pistas y tres fallos anulan el QR."""
        if code is not None and hmac.compare_digest(code, challenge.verification_code):
            return
        challenge.attempts = (challenge.attempts or 0) + 1
        if challenge.attempts >= MAX_CODE_ATTEMPTS:
            challenge.status = "denied"
        self.challenges.save(challenge)
        self.db.commit()          # los intentos se guardan aunque la petición falle
        raise WrongVerificationCodeError()

    # ------------------------------------------------------------------
    # Reclamar el resultado (lo hace la computadora que mostró el QR)
    # ------------------------------------------------------------------

    def claim(self, challenge_id: str, secret: str) -> ClaimResponse:
        challenge = self.challenges.get(challenge_id, lock=True)
        # Sin el secreto correcto es indistinguible de un QR que no existe.
        if challenge is None or not hmac.compare_digest(sha256(secret), challenge.secret_hash):
            raise ChallengeNotFoundError(challenge_id)
        if challenge.status == "claimed":
            raise ChallengeNotFoundError(challenge_id)
        if challenge.expires_at <= _now() and challenge.status != "denied":
            raise ChallengeExpiredError()
        if challenge.status == "pending":
            return ClaimResponse(status="pending")
        if challenge.status == "denied":
            return ClaimResponse(status="denied")

        # status == "approved": se entrega UNA vez.
        if challenge.kind == "enroll":
            response = self._deliver_device(challenge)
        else:
            response = self._deliver_session(challenge)
        challenge.status = "claimed"
        self.challenges.save(challenge)
        self.db.commit()
        return response

    def _deliver_device(self, challenge: AuthChallenge) -> ClaimResponse:
        token = secrets.token_urlsafe(32)
        now = _now()
        device = Device(
            id=generate_id("dv", 20),
            tenant_id=challenge.tenant_id,
            name=challenge.device_name or DEFAULT_DEVICE_NAME,
            token_hash=sha256(token),
            authorized_by=challenge.approved_by,
            user_agent=challenge.user_agent,
            created_at=now,
            expires_at=now + timedelta(hours=settings.DEVICE_TTL_HOURS),
        )
        self.devices.create(device)
        log_operation(
            tenant_id=device.tenant_id,
            user_id=challenge.approved_by,
            action="AUTHORIZE_DEVICE",
            resource_id=device.id,
            after={"name": device.name, "expires_at": device.expires_at.isoformat()},
        )
        return ClaimResponse(status="approved", device_token=token, device_name=device.name, expires_at=device.expires_at)

    def _deliver_session(self, challenge: AuthChallenge) -> ClaimResponse:
        user = self.users.get_by_id(challenge.approved_by, challenge.tenant_id)
        tenant = self.tenants.get_by_id(challenge.tenant_id)
        if user is None or not user.is_active or tenant is None or not tenant.is_active:
            raise ChallengeNotFoundError(challenge.id)

        self.users.record_login(user)
        log_auth_event(email=user.email, success=True, ip_address=challenge.ip_address, reason="qr_unlock")
        log_operation(tenant_id=tenant.id, user_id=user.id, action="LOGIN", resource_id=challenge.device_id)
        token = AuthService._issue_token(user, tenant)
        return ClaimResponse(status="approved", login=AuthService._token_response(token, user, tenant))

    # ------------------------------------------------------------------
    # Entrar en un equipo autorizado con correo o código de empleado
    # ------------------------------------------------------------------

    def login_on_device(self, device: Device, payload: DeviceLoginRequest, ip: Optional[str]) -> TokenResponse:
        identifier = payload.identifier.strip()
        redis_client = get_redis()
        key = identifier.lower()
        lock_key = f"auth:lockout:dev:{device.tenant_id}:{key}"
        attempts_key = f"auth:failed:dev:{device.tenant_id}:{key}"

        if redis_client is not None:
            try:
                if redis_client.exists(lock_key):
                    log_auth_event(email=key, success=False, ip_address=ip, reason="account_locked")
                    raise AccountLockedError(key)
            except AccountLockedError:
                raise
            except Exception:
                logger.warning("Redis lockout check failed; continuing without it", exc_info=True)
                redis_client = None

        user = self._find_in_tenant(device.tenant_id, identifier)
        if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
            self._register_failure(redis_client, attempts_key, lock_key, key)
            log_auth_event(email=key, success=False, ip_address=ip, reason="invalid_credentials")
            raise InvalidCredentialsError(key)

        tenant = self.tenants.get_by_id(user.tenant_id)
        if tenant is None or not tenant.is_active:
            raise InvalidCredentialsError(key)

        if redis_client is not None:
            try:
                redis_client.delete(attempts_key, lock_key)
            except Exception:
                logger.warning("Redis cleanup after login failed", exc_info=True)

        self.users.record_login(user)
        self.db.commit()
        log_auth_event(email=user.email, success=True, ip_address=ip, reason="device_login")
        log_operation(tenant_id=tenant.id, user_id=user.id, action="LOGIN", resource_id=device.id)
        return AuthService._token_response(AuthService._issue_token(user, tenant), user, tenant)

    def _find_in_tenant(self, tenant_id: str, identifier: str):
        """Por correo o por código de empleado, SIEMPRE dentro del negocio del equipo."""
        if "@" in identifier:
            user = self.users.get_by_email(identifier.lower())
            return user if user is not None and user.tenant_id == tenant_id else None
        if re.fullmatch(r"\d{1,8}", identifier):
            return self.users.get_by_employee_code(tenant_id, identifier.zfill(4))
        return None

    @staticmethod
    def _register_failure(redis_client, attempts_key: str, lock_key: str, key: str) -> None:
        if redis_client is None:
            return
        try:
            attempts = redis_client.incr(attempts_key)
            if attempts == 1:
                redis_client.expire(attempts_key, LOCKOUT_SECONDS)
            if attempts >= MAX_FAILED_ATTEMPTS:
                redis_client.set(lock_key, "1", ex=LOCKOUT_SECONDS)
                logger.warning("Device login locked after %s failures: key=%s", attempts, key)
        except Exception:
            logger.warning("Redis failed-attempt bookkeeping failed; continuing", exc_info=True)

    # ------------------------------------------------------------------
    # Lista y baja de equipos
    # ------------------------------------------------------------------

    def list_devices(self, current_user: CurrentUser) -> DeviceList:
        rows = self.devices.list_active(current_user.tenant_id, _now())
        return DeviceList(items=[self._to_info(d, email) for d, email in rows])

    def revoke(self, current_user: CurrentUser, device_id: str) -> None:
        device = self.devices.get_by_id(current_user.tenant_id, device_id)
        if device is None:
            raise ChallengeNotFoundError(device_id)
        self.devices.revoke(device, _now())
        self.db.commit()
        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="REVOKE_DEVICE",
            resource_id=device.id,
            before={"name": device.name},
        )

    @staticmethod
    def _to_info(device: Device, authorized_by: Optional[str]) -> DeviceInfo:
        return DeviceInfo(
            device_id=device.id,
            name=device.name,
            authorized_by=authorized_by,
            user_agent_label=describe_user_agent(device.user_agent),
            created_at=device.created_at,
            expires_at=device.expires_at,
            last_seen_at=device.last_seen_at,
        )

    @staticmethod
    def describe(device: Device) -> DeviceInfo:
        return DeviceService._to_info(device, None)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _visible_pending(self, current_user: CurrentUser, challenge_id: str) -> AuthChallenge:
        challenge = self.challenges.get(challenge_id)
        self._require_pending(current_user, challenge)
        return challenge

    @staticmethod
    def _require_pending(current_user: CurrentUser, challenge: Optional[AuthChallenge]) -> None:
        """
        Existe, sigue pendiente y es de este negocio (si ya lo tiene).

        Un QR de otro negocio es indistinguible de uno que no existe: no se
        confirma a nadie que ese identificador sea real.
        """
        if challenge is None or challenge.status != "pending":
            raise ChallengeNotFoundError("challenge")
        if challenge.kind == "unlock" and challenge.tenant_id != current_user.tenant_id:
            raise ChallengeNotFoundError("challenge")
        if challenge.expires_at <= _now():
            raise ChallengeExpiredError()
