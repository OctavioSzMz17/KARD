"""
Rutas de la caja (B2B). El negocio sale del token, nunca del cuerpo.

POST /api/v1/cash/open          abrir caja con su fondo inicial
GET  /api/v1/cash/current       turno abierto con cifras en vivo (null si está cerrada)
POST /api/v1/cash/close         cerrar con lo contado: congela el corte
GET  /api/v1/cash/sessions      historial de cortes
GET  /api/v1/cash/sessions/{id} un corte (para reimprimirlo)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from cash.models import (
    CashSessionListResponse,
    CashSessionResponse,
    CloseCashRequest,
    OpenCashRequest,
)
from cash.service import CashService
from core.dependencies import CurrentUser, require_permission
from core import permissions as P
from db.database import get_db
from devices.repository import DeviceRepository
from tickets.service import TicketService

router = APIRouter()


@router.get(
    "/current",
    response_model=Optional[CashSessionResponse],
    summary="Turno de caja abierto, con sus cifras en vivo",
)
def current_session(
    current_user: CurrentUser = Depends(require_permission(P.CASH_VIEW)),
    db: Session = Depends(get_db),
):
    """`null` cuando la caja está cerrada: el punto de venta pide abrirla."""
    return CashService(db).current(current_user.tenant_id)


@router.post(
    "/open",
    response_model=CashSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir la caja con su fondo inicial",
)
def open_cash(
    payload: OpenCashRequest,
    current_user: CurrentUser = Depends(require_permission(P.CASH_MANAGE)),
    db: Session = Depends(get_db),
) -> CashSessionResponse:
    return CashService(db).open(current_user, payload)


@router.post(
    "/close",
    response_model=CashSessionResponse,
    summary="Cerrar la caja con el efectivo contado y obtener el corte",
)
def close_cash(
    payload: CloseCashRequest,
    current_user: CurrentUser = Depends(require_permission(P.CASH_MANAGE)),
    db: Session = Depends(get_db),
) -> CashSessionResponse:
    closed = CashService(db).close(current_user, payload)
    # Con el corte termina el turno: los equipos que la gerencia autorizó para
    # el día dejan de estar autorizados, y mañana se vuelven a autorizar.
    DeviceRepository(db).revoke_all(current_user.tenant_id, datetime.now(timezone.utc))
    # Los tickets que se estaban armando en las cajas terminan con el turno.
    TicketService(db).clear_tenant(current_user.tenant_id)
    db.commit()
    return closed


@router.get(
    "/sessions",
    response_model=CashSessionListResponse,
    summary="Historial de turnos, el más reciente primero",
)
def list_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(require_permission(P.CASH_MANAGE)),
    db: Session = Depends(get_db),
) -> CashSessionListResponse:
    return CashService(db).list_sessions(current_user.tenant_id, page, page_size)


@router.get(
    "/sessions/{session_id}",
    response_model=CashSessionResponse,
    summary="Un turno y su corte",
)
def get_session(
    session_id: str,
    current_user: CurrentUser = Depends(require_permission(P.CASH_MANAGE)),
    db: Session = Depends(get_db),
) -> CashSessionResponse:
    return CashService(db).get_session(current_user.tenant_id, session_id)
