"""Esquemas del módulo de equipos autorizados y acceso por QR."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from auth.models import TokenResponse


class ChallengeRequest(BaseModel):
    """
    Lo que manda una computadora para pedir un QR.

    Genera un secreto aleatorio, lo guarda y manda solo su hash: así el
    servidor no puede entregar el resultado a nadie que no tenga el secreto.
    """

    secret_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class ChallengeCreated(BaseModel):
    challenge_id: str
    #: Se muestra junto al QR; quien autoriza lo teclea en el celular.
    verification_code: str
    expires_at: datetime
    expires_in: int


class ChallengeInfo(BaseModel):
    """Lo que ve en el celular quien está a punto de aprobar."""

    kind: Literal["enroll", "unlock"]
    #: Qué equipo pide acceso, en palabras ("Chrome en Windows").
    device_label: str
    ip_address: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    #: En un desbloqueo, el nombre de la caja ("Caja 1").
    device_name: Optional[str] = None
    #: Autorizar un equipo exige teclear el código que se ve en su pantalla.
    requires_code: bool


class ApproveRequest(BaseModel):
    #: Cómo se llamará la caja (solo al autorizar un equipo).
    device_name: Optional[str] = Field(default=None, max_length=60)
    code: Optional[str] = Field(default=None, pattern=r"^\d{4}$")
    #: Si se manda, abre la caja con ese fondo al autorizar (si no hay una abierta).
    open_float: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class ApproveResult(BaseModel):
    status: str = "approved"
    cash_opened: bool = False


class ClaimRequest(BaseModel):
    secret: str = Field(..., min_length=32, max_length=128)


class ClaimResponse(BaseModel):
    status: Literal["pending", "approved", "denied"]
    # Si fue un `enroll` aprobado: el token del equipo. Se entrega UNA sola vez.
    device_token: Optional[str] = None
    device_name: Optional[str] = None
    expires_at: Optional[datetime] = None
    # Si fue un `unlock` aprobado: la sesión de la persona, igual que un inicio de sesión.
    login: Optional[TokenResponse] = None


class DeviceLoginRequest(BaseModel):
    #: Correo o código de empleado.
    identifier: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class DeviceInfo(BaseModel):
    device_id: str
    name: str
    authorized_by: Optional[str] = None
    user_agent_label: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: Optional[datetime] = None


class DeviceList(BaseModel):
    items: list[DeviceInfo]
