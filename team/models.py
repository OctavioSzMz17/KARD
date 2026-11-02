"""Esquemas del módulo de equipo: quién forma parte del negocio y con qué puesto."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

# Los puestos que se pueden dar de alta. El dueño no está: se registra solo,
# al crear el negocio, y nadie puede nombrar a otro dueño desde aquí.
AssignablePosition = Literal["gerente", "subgerente", "encargado_area", "empleado"]


def _strong_password(value: str) -> str:
    if not any(ch.isdigit() for ch in value):
        raise ValueError("La contraseña debe llevar al menos un número")
    if not any(ch.isalpha() for ch in value):
        raise ValueError("La contraseña debe llevar al menos una letra")
    return value


class PositionOption(BaseModel):
    value: str
    label: str


class TeamMember(BaseModel):
    user_id: str
    full_name: Optional[str] = None
    email: EmailStr
    employee_code: Optional[str] = None
    position: str
    position_label: str
    is_active: bool
    last_login: Optional[datetime] = None
    #: El usuario que consulta puede modificar a esta persona (está estrictamente por debajo).
    can_manage: bool


class TeamList(BaseModel):
    items: list[TeamMember]
    #: Puestos que quien consulta puede dar a otros: la pantalla ofrece solo estos.
    assignable_positions: list[PositionOption]


class CreateMemberRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    position: AssignablePosition = "empleado"

    @field_validator("full_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("El nombre no puede estar vacío")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return _strong_password(v)


class UpdateMemberRequest(BaseModel):
    """Todo opcional: solo cambia lo que se manda."""

    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    position: Optional[AssignablePosition] = None
    is_active: Optional[bool] = None

    @field_validator("full_name")
    @classmethod
    def _name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("El nombre no puede estar vacío")
        return v


class ResetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return _strong_password(v)
