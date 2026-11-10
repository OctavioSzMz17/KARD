"""La regla de contraseñas de KARD, en un solo lugar para lo nuevo."""

MIN_LENGTH = 8
MAX_LENGTH = 128


def require_strong(value: str) -> str:
    """
    Una contraseña lleva al menos un número y una letra (más el largo mínimo que ya exige cada formulario).

    Es la misma regla del registro, del alta de equipo y del cambio de contraseña; se deja aquí para
    que lo nuevo no vuelva a copiarla.
    """
    if not any(ch.isdigit() for ch in value):
        raise ValueError("La contraseña debe llevar al menos un número")
    if not any(ch.isalpha() for ch in value):
        raise ValueError("La contraseña debe llevar al menos una letra")
    return value
