"""
Qué productos llevan existencias propias.

En un restaurante el platillo se cocina al momento: no hay "12 chilaquiles en
el refrigerador". Lo que se acaba son los insumos de su receta, y eso ya se
descuenta solo en cada venta. Así que los platillos (`final` de un
restaurante) no tienen stock, y lo único que puede impedir venderlos es que
falte un insumo.

Todo lo demás (los insumos, y los productos de una óptica) sí lleva
existencias: un lente terminado es una pieza que está o no está en el almacén.

Esta es la única regla: el back, las alertas y el panel la consultan aquí.
"""

from typing import Optional


def tracks_stock(business_type: Optional[str], product_type: object) -> bool:
    """`True` si el producto tiene existencias propias que contar y descontar."""
    kind = getattr(product_type, "value", product_type)
    return not (business_type == "restaurant" and kind == "final")
