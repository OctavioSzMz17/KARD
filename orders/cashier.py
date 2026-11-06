"""
Cómo se nombra a quien cobró.

  · En el TICKET que se le da al cliente: nombre y la inicial del apellido ("Eva P.").
    No se le entrega el nombre completo de una persona del negocio a un desconocido.
  · En las vistas INTERNAS (reporte de ventas, corte): el nombre completo y su código.

Quien no tiene nombre capturado (por ejemplo, una cuenta de dueño antigua) se muestra con
la parte local de su correo, como se hacía antes.
"""

from typing import Optional


def _clean(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def _from_email(email: Optional[str]) -> Optional[str]:
    local = (email or "").split("@")[0].strip()
    return local or None


def full_name(name: Optional[str], email: Optional[str] = None) -> Optional[str]:
    """Nombre completo para las vistas internas; sin nombre, la parte local del correo."""
    return _clean(name) or _from_email(email)


def short_name(name: Optional[str], email: Optional[str] = None) -> Optional[str]:
    """
    Para el ticket: el primer nombre y la inicial del último apellido.

        "Eva Prueba"            → "Eva P."
        "Ana María López Ruiz"  → "Ana R."
        "juan"                  → "Juan"
        sin nombre              → la parte local del correo
    """
    cleaned = _clean(name)
    if not cleaned:
        return _from_email(email)
    parts = cleaned.split(" ")
    first = parts[0][:1].upper() + parts[0][1:]
    if len(parts) == 1:
        return first
    return f"{first} {parts[-1][:1].upper()}."
