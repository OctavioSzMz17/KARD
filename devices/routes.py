"""
Equipos autorizados y acceso por QR (rutas delgadas; la regla vive en `DeviceService`).

  Públicas, por diseño (las usa una computadora que aún no tiene sesión):
    POST /device-auth/enroll-challenges        — pedir un QR para autorizar este equipo
    POST /device-auth/challenges/{id}/claim    — recoger el resultado (exige el secreto del equipo)

  Con el token del equipo:
    POST /device-auth/unlock-challenges        — pedir un QR para abrir sesión
    POST /device-auth/login                    — abrir sesión con correo o código de empleado
    GET  /device-auth/current                  — ¿sigue autorizado este equipo?

  Con sesión de una persona (el celular):
    GET  /device-auth/challenges/{id}          — qué voy a aprobar
    POST /device-auth/challenges/{id}/approve  — aprobar (autorizar equipo o abrir sesión)

  Solo gerencia (`devices:authorize`):
    GET    /device-auth/devices                — equipos autorizados hoy
    DELETE /device-auth/devices/{id}           — desautorizar uno
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from auth.models import TokenResponse
from core import permissions as P
from core.dependencies import CurrentUser, require_permission
from db.database import get_db
from db.models import Device
from devices.dependencies import require_device
from devices.models import (
    ApproveRequest,
    ApproveResult,
    ChallengeCreated,
    ChallengeInfo,
    ChallengeRequest,
    ClaimRequest,
    ClaimResponse,
    DeviceInfo,
    DeviceList,
    DeviceLoginRequest,
)
from devices.service import DeviceService

router = APIRouter()


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _user_agent(request: Request) -> Optional[str]:
    return request.headers.get("user-agent")


@router.post(
    "/enroll-challenges",
    response_model=ChallengeCreated,
    status_code=status.HTTP_201_CREATED,
    summary="A computer asks for a QR to be authorized as a register (public)",
)
def create_enroll_challenge(
    payload: ChallengeRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ChallengeCreated:
    return DeviceService(db).create_enroll_challenge(payload.secret_hash, _user_agent(request), _client_ip(request))


@router.post(
    "/unlock-challenges",
    response_model=ChallengeCreated,
    status_code=status.HTTP_201_CREATED,
    summary="An authorized register asks for a QR to open a session",
)
def create_unlock_challenge(
    payload: ChallengeRequest,
    request: Request,
    device: Device = Depends(require_device),
    db: Session = Depends(get_db),
) -> ChallengeCreated:
    return DeviceService(db).create_unlock_challenge(device, payload.secret_hash, _user_agent(request), _client_ip(request))


@router.post(
    "/challenges/{challenge_id}/claim",
    response_model=ClaimResponse,
    summary="The computer that showed the QR collects the result (needs its secret)",
)
def claim_challenge(
    challenge_id: str,
    payload: ClaimRequest,
    db: Session = Depends(get_db),
) -> ClaimResponse:
    return DeviceService(db).claim(challenge_id, payload.secret)


@router.get(
    "/challenges/{challenge_id}",
    response_model=ChallengeInfo,
    summary="What a phone is about to approve",
)
def challenge_info(
    challenge_id: str,
    current_user: CurrentUser = Depends(require_permission(P.DEVICES_AUTHORIZE, P.DEVICES_UNLOCK)),
    db: Session = Depends(get_db),
) -> ChallengeInfo:
    return DeviceService(db).info(current_user, challenge_id)


@router.post(
    "/challenges/{challenge_id}/approve",
    response_model=ApproveResult,
    summary="Approve a QR: authorize a register (management) or open a session (any staff)",
)
def approve_challenge(
    challenge_id: str,
    payload: ApproveRequest,
    current_user: CurrentUser = Depends(require_permission(P.DEVICES_AUTHORIZE, P.DEVICES_UNLOCK)),
    db: Session = Depends(get_db),
) -> ApproveResult:
    return DeviceService(db).approve(current_user, challenge_id, payload)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Open a session on an authorized register with email or employee code",
)
def device_login(
    payload: DeviceLoginRequest,
    request: Request,
    device: Device = Depends(require_device),
    db: Session = Depends(get_db),
) -> TokenResponse:
    return DeviceService(db).login_on_device(device, payload, _client_ip(request))


@router.get(
    "/current",
    response_model=DeviceInfo,
    summary="Is this register still authorized?",
)
def current_device(device: Device = Depends(require_device)) -> DeviceInfo:
    return DeviceService.describe(device)


@router.get(
    "/devices",
    response_model=DeviceList,
    summary="Registers authorized today",
)
def list_devices(
    current_user: CurrentUser = Depends(require_permission(P.DEVICES_AUTHORIZE)),
    db: Session = Depends(get_db),
) -> DeviceList:
    return DeviceService(db).list_devices(current_user)


@router.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw a register's authorization",
)
def revoke_device(
    device_id: str,
    current_user: CurrentUser = Depends(require_permission(P.DEVICES_AUTHORIZE)),
    db: Session = Depends(get_db),
) -> None:
    DeviceService(db).revoke(current_user, device_id)
