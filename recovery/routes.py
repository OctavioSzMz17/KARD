"""
Recuperar la contraseña por correo (rutas delgadas; la regla vive en `PasswordRecoveryService`).

Públicas por diseño: las usa quien no puede iniciar sesión.

  POST /auth/forgot-password   — pide el enlace; responde siempre lo mismo, exista o no el correo
  POST /auth/reset-password    — cambia la contraseña con el enlace del correo
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.orm import Session

from db.database import get_db
from notifications.service import NotificationService
from recovery.models import ForgotPasswordRequest, MessageResponse, ResetPasswordRequest
from recovery.service import PasswordRecoveryService

router = APIRouter()

#: Lo que se contesta SIEMPRE al pedir el enlace.
FORGOT_MESSAGE = "Si ese correo está registrado, te enviamos un enlace para crear una contraseña nueva. Dura 1 hora."
RESET_MESSAGE = "Listo: tu contraseña cambió. Ya puedes iniciar sesión con la nueva."


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask for a link to reset the password (always the same answer)",
)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> MessageResponse:
    pending = PasswordRecoveryService(db).request(payload.email, payload.audience, _client_ip(request))
    if pending is not None:
        # Después de responder: así la respuesta tarda lo mismo exista o no el correo.
        background.add_task(NotificationService.password_reset, email=pending.to, name=pending.name, link=pending.link)
    return MessageResponse(message=FORGOT_MESSAGE)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password with the link from the email",
)
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> MessageResponse:
    done = PasswordRecoveryService(db).reset(payload.token, payload.password, _client_ip(request))
    background.add_task(NotificationService.password_changed, email=done.to, name=done.name)
    return MessageResponse(message=RESET_MESSAGE)
