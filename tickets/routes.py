"""
Ticket compartido de una caja (rutas delgadas; la regla vive en `tickets/logic.py`).

  GET  /tickets/current   — el ticket de MI caja; con `since` y `uid`, 204 si no hay nada nuevo
  POST /tickets/ops       — una operación sobre el ticket (agregar, cambiar cantidad, quitar, vaciar)

Las usan la computadora de la caja (por su token de equipo) y el celular de la persona
que opera esa caja. Una sesión sin caja recibe `register: null` y su ticket se queda local.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from core import permissions as P
from core.dependencies import CurrentUser, require_permission
from core.exceptions import NoRegisterError, ProductNotFoundError
from db.database import get_db
from db.models import Device, Product
from devices.dependencies import get_optional_device
from tickets.logic import public
from tickets.models import RegisterInfo, TicketOp, TicketResponse, TicketState
from tickets.service import TicketService

router = APIRouter()


def _response(register: Device, state: dict) -> TicketResponse:
    return TicketResponse(
        register=RegisterInfo(id=register.id, name=register.name),
        ticket=TicketState(**public(state)),
    )


@router.get(
    "/current",
    response_model=TicketResponse,
    responses={204: {"description": "Sin cambios desde `since` (mismo `uid` y misma revisión)"}},
    summary="The shared ticket of the register this session operates",
)
def current_ticket(
    since: Optional[int] = Query(default=None, ge=0, description="Revisión que la pantalla ya tiene"),
    uid: Optional[str] = Query(default=None, max_length=40, description="Ticket que la pantalla ya tiene"),
    current_user: CurrentUser = Depends(require_permission(P.POS_SELL)),
    device: Optional[Device] = Depends(get_optional_device),
    db: Session = Depends(get_db),
):
    svc = TicketService(db)
    register = svc.register_for(current_user, device)
    if register is None:
        return TicketResponse()

    state = svc.current(register.tenant_id, register.id)
    if since is not None and uid == state["uid"] and since == state["rev"]:
        return Response(status_code=204)
    return _response(register, state)


@router.post(
    "/ops",
    response_model=TicketResponse,
    summary="Apply one change to the shared ticket and get the resulting ticket",
)
def apply_operation(
    op: TicketOp,
    current_user: CurrentUser = Depends(require_permission(P.POS_SELL)),
    device: Optional[Device] = Depends(get_optional_device),
    db: Session = Depends(get_db),
) -> TicketResponse:
    svc = TicketService(db)
    register = svc.register_for(current_user, device)
    if register is None:
        raise NoRegisterError()

    # Solo se agrega lo que este negocio vende: un identificador inventado no llega al ticket.
    if op.type == "add":
        exists = (
            db.query(Product.id)
            .filter(
                Product.id == op.product_id,
                Product.tenant_id == current_user.tenant_id,
                Product.is_deleted.is_(False),
                Product.product_type == "final",
            )
            .first()
        )
        if exists is None:
            raise ProductNotFoundError(op.product_id)

    state = svc.apply(register.tenant_id, register.id, op.model_dump())
    return _response(register, state)
