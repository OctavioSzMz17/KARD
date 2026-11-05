"""Esquemas del ticket compartido de una caja."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

OP_ID_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"


class TicketOp(BaseModel):
    """Una operación sobre el ticket. `op_id` la hace repetible sin efecto doble."""

    op_id: str = Field(..., pattern=OP_ID_PATTERN)
    type: Literal["add", "set_quantity", "remove", "clear"]
    product_id: Optional[str] = Field(default=None, min_length=1, max_length=60)
    quantity: Optional[int] = Field(default=None, ge=1, le=999)
    #: Solo para `clear`: vacía únicamente si el ticket sigue siendo ese.
    uid: Optional[str] = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _needs_what_it_acts_on(self) -> "TicketOp":
        if self.type in ("add", "set_quantity", "remove") and not self.product_id:
            raise ValueError(f"'{self.type}' necesita un producto")
        if self.type == "set_quantity" and self.quantity is None:
            raise ValueError("'set_quantity' necesita la cantidad")
        return self


class TicketLine(BaseModel):
    product_id: str
    quantity: int


class TicketState(BaseModel):
    uid: str
    rev: int
    lines: list[TicketLine]


class RegisterInfo(BaseModel):
    id: str
    name: str


class TicketResponse(BaseModel):
    """`register` es null si esta sesión no opera ninguna caja: el ticket se queda en su pantalla."""

    register: Optional[RegisterInfo] = None
    ticket: Optional[TicketState] = None
