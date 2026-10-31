"""
Cabeceras de caché HTTP para las respuestas públicas (tienda y explorar).

Son iguales para todas las personas que las piden, así que pueden guardarse en
el navegador (o en un CDN) un minuto sin preguntar, y después revalidarse con
`If-None-Match` para recibir un 304 sin cuerpo si nada cambió.
"""

import hashlib
from typing import Union

from fastapi import Request, Response, status
from pydantic import BaseModel

# `public`: nada depende de quién pregunta. `stale-while-revalidate` deja que el
# navegador muestre la copia vieja al instante mientras la refresca en segundo
# plano, así nadie espera una revalidación.
PUBLIC_CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=300"


def with_http_cache(request: Request, response: Response, model: BaseModel) -> Union[BaseModel, Response]:
    """
    Agrega `ETag` + `Cache-Control`, o responde 304 si la copia del cliente está al día.

    El ETag es un hash de la respuesta serializada: cambia exactamente cuando
    cambia el contenido, incluso si una invalidación de Redis reconstruye datos
    idénticos (mismo hash, sigue siendo 304).
    """
    etag = 'W/"' + hashlib.sha1(model.model_dump_json().encode("utf-8")).hexdigest()[:24] + '"'
    headers = {"ETag": etag, "Cache-Control": PUBLIC_CACHE_CONTROL}

    sent = [t.strip() for t in request.headers.get("if-none-match", "").split(",") if t.strip()]
    if etag in sent or "*" in sent:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    response.headers.update(headers)
    return model
