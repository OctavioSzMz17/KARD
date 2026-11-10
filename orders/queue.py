"""Cómo se lee el estado de un pedido en el tablero de órdenes."""

#: Los estados que el tablero muestra, en el orden en que avanza un pedido.
QUEUE_STATES = ("preparing", "ready", "in_transit", "completed", "canceled")


def queue_state(status: str, fulfillment_status: str) -> str:
    """
    El estado de un pedido en una sola palabra.

    Pago y entrega son ejes independientes, pero el tablero necesita ubicar cada pedido en una
    columna: lo cancelado gana sobre todo lo demás, y `fulfilled` (un valor histórico del pago que
    significaba "ya entregado") cuenta como completado aunque su entrega no se haya movido.
    """
    if status == "canceled" or fulfillment_status == "canceled":
        return "canceled"
    if status == "fulfilled":
        return "completed"
    return fulfillment_status
