"""
Equipo del negocio (B2B). Rutas delgadas: toda la regla vive en `TeamService`.

  GET    /api/v1/team                     — el equipo y los puestos que puedo dar
  POST   /api/v1/team                     — registrar a una persona
  PATCH  /api/v1/team/{user_id}           — cambiar nombre, puesto o dar de baja
  POST   /api/v1/team/{user_id}/password  — restablecer su contraseña

Todas exigen `team:manage` (dueño, gerente y subgerente); además, cada acción
sobre una persona exige estar por encima de ella (ver `TeamService`).
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from core import permissions as P
from core.dependencies import CurrentUser, require_permission
from db.database import get_db
from team.models import (
    CreateMemberRequest,
    ResetPasswordRequest,
    TeamList,
    TeamMember,
    UpdateMemberRequest,
)
from team.service import TeamService

router = APIRouter()


@router.get("", response_model=TeamList, summary="Team of the caller's business")
def list_team(
    current_user: CurrentUser = Depends(require_permission(P.TEAM_MANAGE)),
    db: Session = Depends(get_db),
) -> TeamList:
    return TeamService(db).list_team(current_user)


@router.post(
    "",
    response_model=TeamMember,
    status_code=status.HTTP_201_CREATED,
    summary="Register a person on the team",
)
def create_member(
    payload: CreateMemberRequest,
    current_user: CurrentUser = Depends(require_permission(P.TEAM_MANAGE)),
    db: Session = Depends(get_db),
) -> TeamMember:
    return TeamService(db).create_member(current_user, payload)


@router.patch("/{user_id}", response_model=TeamMember, summary="Change a team member")
def update_member(
    user_id: str,
    payload: UpdateMemberRequest,
    current_user: CurrentUser = Depends(require_permission(P.TEAM_MANAGE)),
    db: Session = Depends(get_db),
) -> TeamMember:
    return TeamService(db).update_member(current_user, user_id, payload)


@router.post(
    "/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset a team member's password",
)
def reset_password(
    user_id: str,
    payload: ResetPasswordRequest,
    current_user: CurrentUser = Depends(require_permission(P.TEAM_MANAGE)),
    db: Session = Depends(get_db),
) -> None:
    TeamService(db).reset_password(current_user, user_id, payload)
