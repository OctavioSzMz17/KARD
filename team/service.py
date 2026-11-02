"""
Lógica del equipo del negocio: registrar personas, cambiar su puesto, darlas de
baja y restablecer su contraseña.

La regla que gobierna todo es una sola (`core.permissions.can_manage`): solo
se puede actuar sobre alguien ESTRICTAMENTE por debajo en la jerarquía. De ahí
salen, sin reglas aparte, que nadie se sube el puesto, nadie toca a su par o a
su superior y el dueño nunca queda a merced de otro.
"""

import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit_logging.audit import log_operation
from auth.repository import UserRepository
from core.dependencies import CurrentUser
from core.exceptions import EmailAlreadyExistsError, TeamHierarchyError, UserNotFoundError
from core.permissions import (
    POSITION_LABELS,
    assignable_positions,
    can_manage,
    level_of,
    position_of,
    title_from_position,
)
from core.security import hash_password
from db.models import User
from team.models import (
    CreateMemberRequest,
    PositionOption,
    ResetPasswordRequest,
    TeamList,
    TeamMember,
    UpdateMemberRequest,
)
from utils.helpers import generate_id

logger = logging.getLogger("kard.team")

# Dos altas a la vez pueden calcular el mismo código; el índice único lo detecta
# y se reintenta con el siguiente.
_CODE_RETRIES = 4


class TeamService:
    def __init__(self, db: Session):
        self.db = db
        self.users = UserRepository(db)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def list_team(self, current_user: CurrentUser) -> TeamList:
        members = self.users.list_for_tenant(current_user.tenant_id)
        # Más autoridad primero y, dentro de cada nivel, por código.
        members.sort(key=lambda u: (level_of(position_of(u.role, u.job_title)), u.employee_code or ""))
        return TeamList(
            items=[self._to_member(u, current_user) for u in members],
            assignable_positions=[
                PositionOption(value=p, label=POSITION_LABELS[p])
                for p in assignable_positions(current_user.position)
            ],
        )

    # ------------------------------------------------------------------
    # Alta
    # ------------------------------------------------------------------

    def create_member(self, current_user: CurrentUser, payload: CreateMemberRequest) -> TeamMember:
        if payload.position not in assignable_positions(current_user.position):
            raise TeamHierarchyError(
                f"Con tu puesto no puedes registrar a alguien como {POSITION_LABELS[payload.position].lower()}"
            )

        email = payload.email.lower()
        if self.users.get_by_email(email) is not None:
            raise EmailAlreadyExistsError(email)

        password_hash = hash_password(payload.password)
        user: Optional[User] = None

        for _ in range(_CODE_RETRIES):
            user = User(
                id=generate_id("u"),
                tenant_id=current_user.tenant_id,
                email=email,
                password_hash=password_hash,
                role="staff",
                job_title=title_from_position(payload.position),
                full_name=payload.full_name,
                employee_code=self.users.next_employee_code(current_user.tenant_id),
                is_active=True,
            )
            try:
                self.users.create(user)
                self.db.commit()
                break
            except IntegrityError as exc:
                self.db.rollback()
                if "uq_users_tenant_employee_code" in str(getattr(exc, "orig", exc)):
                    user = None
                    continue            # otro alta tomó ese código: se pide el siguiente
                raise EmailAlreadyExistsError(email)
        if user is None:
            raise RuntimeError("No se pudo asignar un código de empleado")

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="CREATE_TEAM_MEMBER",
            resource_id=user.id,
            after={"email": user.email, "position": payload.position, "employee_code": user.employee_code},
        )
        return self._to_member(user, current_user)

    # ------------------------------------------------------------------
    # Cambios
    # ------------------------------------------------------------------

    def update_member(self, current_user: CurrentUser, user_id: str, payload: UpdateMemberRequest) -> TeamMember:
        target = self._require_manageable(current_user, user_id)
        before = {"full_name": target.full_name, "position": position_of(target.role, target.job_title),
                  "is_active": target.is_active}

        if payload.full_name is not None:
            target.full_name = payload.full_name

        if payload.position is not None:
            if payload.position not in assignable_positions(current_user.position):
                raise TeamHierarchyError(
                    f"Con tu puesto no puedes dejar a alguien como {POSITION_LABELS[payload.position].lower()}"
                )
            target.job_title = title_from_position(payload.position)

        if payload.is_active is not None:
            target.is_active = payload.is_active

        self.users.save(target)
        self.db.commit()

        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="UPDATE_TEAM_MEMBER",
            resource_id=target.id,
            before=before,
            after={"full_name": target.full_name, "position": position_of(target.role, target.job_title),
                   "is_active": target.is_active},
        )
        return self._to_member(target, current_user)

    def reset_password(self, current_user: CurrentUser, user_id: str, payload: ResetPasswordRequest) -> None:
        target = self._require_manageable(current_user, user_id)
        target.password_hash = hash_password(payload.password)
        self.users.save(target)
        self.db.commit()

        # La contraseña nunca va a la bitácora, ni siquiera cifrada.
        log_operation(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="RESET_TEAM_PASSWORD",
            resource_id=target.id,
        )

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _require_manageable(self, current_user: CurrentUser, user_id: str) -> User:
        """
        La persona, del mismo negocio, y solo si está por debajo de quien actúa.

        Se busca por (negocio, id): alguien de otro negocio es indistinguible de
        alguien que no existe (404), no se confirma que exista.
        """
        target = self.users.get_by_id(user_id, current_user.tenant_id)
        if target is None:
            raise UserNotFoundError(user_id)

        if not can_manage(current_user.position, position_of(target.role, target.job_title)):
            raise TeamHierarchyError("No puedes modificar a alguien de tu mismo nivel o superior")
        return target

    @staticmethod
    def _to_member(user: User, current_user: CurrentUser) -> TeamMember:
        position = position_of(user.role, user.job_title)
        return TeamMember(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            employee_code=user.employee_code,
            position=position,
            position_label=POSITION_LABELS[position],
            is_active=user.is_active,
            last_login=user.last_login,
            can_manage=can_manage(current_user.position, position),
        )
