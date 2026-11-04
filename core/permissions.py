"""
Quién puede hacer qué dentro de un negocio.

Dos ideas separadas, a propósito:

  * ROL (`business_admin` | `staff`): lo que ya existe en la base. Dice si la
    cuenta es del dueño o del personal. No cambia.
  * PUESTO (gerente, subgerente, encargado de área…): identifica a la persona
    dentro del negocio. Es una columna aparte (`users.job_title`).

De la pareja rol + puesto sale UNA sola cosa que importa para autorizar: el
PUESTO EFECTIVO (`position`), y de él, el paquete de permisos y el nivel
jerárquico. Así la regla vive en este archivo y no repartida por las rutas.

    dueño ─ gerente ─ subgerente ─ encargado de área ─ empleado
    (más autoridad)                                   (menos)

Principios:
  * Los permisos se comprueban en el SERVIDOR (`require_permission`). Esconder
    un botón en la pantalla no protege nada: quien llame a la API directo
    se saltaría el menú.
  * Nadie puede crear, cambiar ni dar de baja a alguien de su mismo nivel o de
    uno superior, ni subirse el puesto a sí mismo.
"""

from typing import Optional

# ── Puestos efectivos (de más a menos autoridad) ─────────────────────
OWNER = "dueno"
MANAGER = "gerente"
ASSISTANT_MANAGER = "subgerente"
AREA_LEAD = "encargado_area"
EMPLOYEE = "empleado"

#: De más a menos autoridad. La posición en la lista ES el nivel (0 = dueño).
POSITIONS: tuple[str, ...] = (OWNER, MANAGER, ASSISTANT_MANAGER, AREA_LEAD, EMPLOYEE)

#: Puestos que se guardan en `users.job_title`. El dueño y el empleado no lo llevan.
JOB_TITLES: tuple[str, ...] = (MANAGER, ASSISTANT_MANAGER, AREA_LEAD)

POSITION_LABELS: dict[str, str] = {
    OWNER: "Dueño",
    MANAGER: "Gerente",
    ASSISTANT_MANAGER: "Subgerente",
    AREA_LEAD: "Encargado de área",
    EMPLOYEE: "Empleado",
}

# ── Permisos ─────────────────────────────────────────────────────────
POS_SELL = "pos:sell"                    # cobrar en el mostrador
PRODUCTS_READ = "products:read"          # ver el catálogo
PRODUCTS_WRITE = "products:write"        # crear, editar, borrar; imágenes y recetas
INVENTORY_ADJUST = "inventory:adjust"    # ajustar existencias (surtir, dar de baja)
CASH_VIEW = "cash:view"                  # saber si la caja está abierta (para cobrar)
CASH_MANAGE = "cash:manage"              # abrir y cerrar caja, ver cortes
SALES_READ = "sales:read"                # reporte de ventas
SALES_MANAGE = "sales:manage"            # cancelar o reembolsar una venta
TEAM_MANAGE = "team:manage"              # registrar y administrar al equipo
SETTINGS_MANAGE = "settings:manage"      # personalización, perfil, formas de entrega
BILLING_MANAGE = "billing:manage"        # suscripción y plan
DASHBOARD_VIEW = "dashboard:view"        # el resumen del panel (solo lo usa la pantalla)
DEVICES_AUTHORIZE = "devices:authorize"  # autorizar una computadora como caja y ver/retirar las autorizadas
DEVICES_UNLOCK = "devices:unlock"        # abrir sesión propia en una caja escaneando su QR

ALL_PERMISSIONS: frozenset[str] = frozenset({
    POS_SELL, PRODUCTS_READ, PRODUCTS_WRITE, INVENTORY_ADJUST, CASH_VIEW, CASH_MANAGE,
    SALES_READ, SALES_MANAGE, TEAM_MANAGE, SETTINGS_MANAGE, BILLING_MANAGE, DASHBOARD_VIEW,
    DEVICES_AUTHORIZE, DEVICES_UNLOCK,
})

# Lo que hace cualquiera del personal: cobrar y consultar el catálogo.
_EMPLOYEE = frozenset({POS_SELL, PRODUCTS_READ, CASH_VIEW, DEVICES_UNLOCK})

# Gerente y subgerente comparten paquete: la diferencia entre ellos es el nivel
# (el gerente puede dar de alta a un subgerente; el subgerente no).
_SUPERVISOR = _EMPLOYEE | {
    PRODUCTS_WRITE, INVENTORY_ADJUST, CASH_MANAGE, SALES_READ, SALES_MANAGE,
    TEAM_MANAGE, DASHBOARD_VIEW, DEVICES_AUTHORIZE,
}

_BY_POSITION: dict[str, frozenset[str]] = {
    OWNER: ALL_PERMISSIONS,
    MANAGER: _SUPERVISOR,
    ASSISTANT_MANAGER: _SUPERVISOR,
    AREA_LEAD: _EMPLOYEE | {INVENTORY_ADJUST},
    EMPLOYEE: _EMPLOYEE,
}


def position_of(role: str, job_title: Optional[str]) -> str:
    """
    Puesto efectivo de una cuenta.

    `saas_admin` (la plataforma) se trata como dueño: antes de esto no tenía
    ninguna restricción sobre los datos de un negocio y no se la quitamos aquí.
    Un puesto desconocido en la base (dato corrupto) cae al más bajo, nunca al
    más alto: ante la duda, menos permisos.
    """
    if role in ("business_admin", "saas_admin"):
        return OWNER
    if job_title in JOB_TITLES:
        return job_title  # type: ignore[return-value]
    return EMPLOYEE


def level_of(position: str) -> int:
    """Nivel jerárquico: 0 es el dueño; un número MAYOR es menos autoridad."""
    return POSITIONS.index(position)


def permissions_of(position: str) -> frozenset[str]:
    return _BY_POSITION.get(position, _EMPLOYEE)


def can_manage(actor_position: str, target_position: str) -> bool:
    """
    ¿Puede `actor` administrar (cambiar, dar de baja) a `target`?

    Solo si el objetivo está ESTRICTAMENTE por debajo. Esto cubre tres cosas de
    una vez: nadie toca a su par ni a su superior, nadie se modifica a sí mismo
    (su nivel es igual al suyo) y el dueño nunca queda a merced de nadie.
    """
    return level_of(actor_position) < level_of(target_position)


def assignable_positions(actor_position: str) -> list[str]:
    """Puestos que `actor` puede dar a otra persona: los estrictamente inferiores al suyo."""
    return [p for p in POSITIONS if can_manage(actor_position, p)]


def title_from_position(position: str) -> Optional[str]:
    """Valor para `users.job_title`: solo gerente, subgerente y encargado lo llevan."""
    return position if position in JOB_TITLES else None
