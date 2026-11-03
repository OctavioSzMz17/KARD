"""
Cobro idempotente: reconocer cuándo una petición de cobro es el MISMO cobro.

El cliente manda una llave por ticket (cabecera `Idempotency-Key`). Aquí vive
lo que no depende de la base ni de FastAPI:

  · qué forma debe tener la llave, y
  · la huella de la petición, para distinguir un reintento (misma llave, mismo
    contenido) de un error del cliente (misma llave, contenido distinto).
"""

import hashlib
import json
import re

from orders.models import CreatePosSaleRequest

# Un UUID (36) o un token aleatorio del navegador caben de sobra. El mínimo
# evita llaves tan cortas que dos tickets distintos puedan chocar por azar.
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


def is_valid_key(key: str) -> bool:
    return bool(KEY_PATTERN.fullmatch(key))


def request_fingerprint(payload: CreatePosSaleRequest, user_id: str) -> str:
    """
    SHA-256 de la petición y de quién cobra.

    Incluir a la persona evita que otro empleado, conociendo o adivinando la
    llave, reciba el ticket de una venta que no cobró.

    La serialización es canónica (claves ordenadas, sin espacios): el mismo
    contenido siempre da la misma huella, sin importar el orden de las claves
    del JSON que mandó el navegador.
    """
    canonical = json.dumps(
        {"user": user_id, "sale": payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
