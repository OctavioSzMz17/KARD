"""Dependencias de FastAPI para el equipo desde el que llega la petición."""

from typing import Optional

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from core.exceptions import DeviceNotAuthorizedError
from db.database import get_db
from db.models import Device
from devices.repository import DeviceRepository
from devices.service import _now, sha256

#: Cabecera con el token del equipo. La pone el navegador de la caja registradora.
DEVICE_HEADER = "X-Device-Token"


# `def`, no `async def`: consulta la base de forma síncrona (ver `core/dependencies.py`).
def get_optional_device(
    x_device_token: Optional[str] = Header(default=None, alias=DEVICE_HEADER),
    db: Session = Depends(get_db),
) -> Optional[Device]:
    """
    El equipo autorizado que hace la petición, o None si no manda token o el
    token ya no sirve (revocado, vencido o inexistente). No distingue entre
    esos casos: a quien lo usa le da igual por qué no está autorizado.
    """
    if not x_device_token:
        return None
    repo = DeviceRepository(db)
    now = _now()
    device = repo.get_valid_by_token_hash(sha256(x_device_token), now)
    if device is not None:
        repo.touch(device, now)
    return device


def require_device(device: Optional[Device] = Depends(get_optional_device)) -> Device:
    if device is None:
        raise DeviceNotAuthorizedError("Este equipo no está autorizado hoy")
    return device
