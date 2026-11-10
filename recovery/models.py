"""Esquemas de la recuperación de contraseña por correo."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from core import passwords


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    # De qué lado de la plataforma es la cuenta: una persona del negocio o un cliente de la tienda.
    audience: Literal["negocio", "cliente"] = "negocio"


class ResetPasswordRequest(BaseModel):
    # El enlace del correo: texto seguro para URL. Se limita la forma para no procesar basura.
    token: str = Field(..., min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(..., min_length=passwords.MIN_LENGTH, max_length=passwords.MAX_LENGTH)

    @field_validator("password")
    @classmethod
    def _strong(cls, value: str) -> str:
        return passwords.require_strong(value)


class MessageResponse(BaseModel):
    message: str
