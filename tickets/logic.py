"""
Reglas del ticket compartido de una caja, sin base de datos ni Redis.

El ticket es una lista de líneas (producto y cantidad) con un número de
revisión que sube con cada cambio. La caja y el celular mandan operaciones y
reciben el estado completo; así "borrar en la laptop" y "agregar en el celular"
son lo mismo: cambios sobre un estado compartido.

Las operaciones están pensadas para NO chocar entre sí:

  add            suma cantidad si el producto ya está (dos escáneres del mismo
                 producto a la vez dan dos unidades, no una).
  set_quantity   apunta a un producto; si ya no está, no hace nada.
  remove         igual: quitar algo que ya no está no es un error.
  clear          vacía y estrena identificador. Con `uid`, solo si sigue siendo
                 ESE ticket: un "vaciar" tardío de un ticket ya cobrado no debe
                 borrar el siguiente.

Cada operación trae un `op_id`; una repetida (reintento de red) no se aplica dos veces.
"""

import secrets
from typing import Any

from core.exceptions import TicketFullError

MAX_LINES = 100
MAX_QUANTITY = 999

#: Cuántas llaves de operación se recuerdan para ignorar repeticiones.
OPS_MEMORY = 40

State = dict[str, Any]


def new_uid() -> str:
    """Identificador de un ticket. Cumple la forma de `Idempotency-Key` (16 a 80 de A-Za-z0-9_-)."""
    return "tk_" + secrets.token_urlsafe(16)


def empty_state() -> State:
    return {"uid": new_uid(), "rev": 0, "lines": [], "ops": []}


def placeholder_state(device_id: str) -> State:
    """
    Lo que se muestra de una caja que aún no tiene ticket. No se guarda: su
    identificador es estable para que las pantallas no lo vean cambiar en cada consulta.
    """
    return {"uid": f"tk_{device_id}", "rev": 0, "lines": [], "ops": []}


def public(state: State) -> dict[str, Any]:
    """Lo que ven las pantallas: sin la memoria de operaciones."""
    return {"uid": state["uid"], "rev": state["rev"], "lines": [dict(line) for line in state["lines"]]}


def apply_op(state: State, op: dict[str, Any]) -> tuple[State, bool]:
    """
    Aplica una operación y devuelve `(estado_nuevo, cambió)`. No modifica `state`.

    Si no cambia nada (repetida, o apunta a algo que ya no existe) devuelve el
    mismo estado y `False`, sin subir la revisión.
    """
    if op["op_id"] in state["ops"]:
        return state, False

    lines = [dict(line) for line in state["lines"]]
    uid = state["uid"]
    kind = op["type"]
    changed = False

    if kind == "add":
        qty = op.get("quantity") or 1
        found = next((line for line in lines if line["product_id"] == op["product_id"]), None)
        if found is not None:
            new_qty = min(found["quantity"] + qty, MAX_QUANTITY)
            changed = new_qty != found["quantity"]
            found["quantity"] = new_qty
        else:
            if len(lines) >= MAX_LINES:
                raise TicketFullError()
            lines.append({"product_id": op["product_id"], "quantity": min(qty, MAX_QUANTITY)})
            changed = True

    elif kind == "set_quantity":
        found = next((line for line in lines if line["product_id"] == op["product_id"]), None)
        if found is not None:
            new_qty = min(op["quantity"], MAX_QUANTITY)
            changed = new_qty != found["quantity"]
            found["quantity"] = new_qty

    elif kind == "remove":
        kept = [line for line in lines if line["product_id"] != op["product_id"]]
        changed = len(kept) != len(lines)
        lines = kept

    elif kind == "clear":
        if op.get("uid") in (None, uid):
            lines = []
            uid = new_uid()
            changed = True

    if not changed:
        return state, False

    ops = (state["ops"] + [op["op_id"]])[-OPS_MEMORY:]
    return {"uid": uid, "rev": state["rev"] + 1, "lines": lines, "ops": ops}, True
