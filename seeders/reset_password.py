"""
Utilidad de desarrollo — restablecer la contraseña de una cuenta.

bcrypt es de una sola vía: una contraseña perdida no se recupera, se
reemplaza. Este script genera un hash nuevo (bcrypt 12 rounds, el mismo
`hash_password` que usa la app) y lo escribe en la base.

Uso:
    python seeders/reset_password.py --list              # ver qué cuentas existen
    python seeders/reset_password.py admin@negocio.com   # cambiar su contraseña
    python seeders/reset_password.py cliente@mail.com --consumer
    python seeders/reset_password.py --all               # todas las cuentas a la vez

Por defecto la contraseña se pide por prompt oculto (getpass), para que no
quede en el historial del shell ni en la lista de procesos. `--password`
existe solo para automatizar entornos de desarrollo: úsalo con contraseñas
desechables, nunca con una real.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.security import hash_password
from db.database import SessionLocal
from db.models import Consumer, Tenant, User

MIN_LENGTH = 8


def _list_accounts(db) -> None:
    """Muestra todas las cuentas B2B y B2C con sus datos identificatorios."""
    users = db.query(User).order_by(User.created_at).all()
    print(f"\nUsuarios de negocio (B2B) — {len(users)}:")
    if not users:
        print("  (ninguno)")
    for u in users:
        tenant = db.query(Tenant).filter(Tenant.id == u.tenant_id).first()
        estado = "activo" if u.is_active else "INACTIVO"
        negocio = tenant.business_name if tenant else "(tenant no encontrado)"
        print(f"  {u.email:<40} {u.role:<15} {estado:<9} {negocio}")

    consumers = db.query(Consumer).order_by(Consumer.created_at).all()
    print(f"\nClientes (B2C) — {len(consumers)}:")
    if not consumers:
        print("  (ninguno)")
    for c in consumers:
        estado = "activo" if c.is_active else "INACTIVO"
        print(f"  {c.email:<40} {estado}")
    print()


def _validate(password: str) -> str | None:
    """Devuelve el motivo por el que la contraseña no sirve, o None si es válida."""
    if len(password) < MIN_LENGTH:
        return f"Debe tener al menos {MIN_LENGTH} caracteres."
    if not any(ch.isdigit() for ch in password):
        return "Debe incluir al menos un número."
    if not any(ch.isalpha() for ch in password):
        return "Debe incluir al menos una letra."
    return None


def _prompt_password() -> str:
    """Pide la contraseña dos veces y valida las mismas reglas que el registro."""
    while True:
        password = getpass.getpass("Nueva contraseña: ")
        error = _validate(password)
        if error:
            print(f"  {error}")
            continue
        if password != getpass.getpass("Confirmar contraseña: "):
            print("  No coinciden, intenta de nuevo.")
            continue
        return password


def _reset_all(db, password: str) -> int:
    """
    Aplica la misma contraseña a todas las cuentas (B2B y B2C).

    Cada cuenta se hashea por separado: bcrypt genera un salt distinto en
    cada llamada, así que los hashes almacenados no se repiten aunque la
    contraseña sea la misma.
    """
    users = db.query(User).order_by(User.created_at).all()
    consumers = db.query(Consumer).order_by(Consumer.created_at).all()

    if not users and not consumers:
        print("No hay ninguna cuenta en la base.")
        return 1

    for user in users:
        user.password_hash = hash_password(password)
        print(f"  actualizado (B2B)  {user.email}")

    for consumer in consumers:
        consumer.hashed_password = hash_password(password)
        print(f"  actualizado (B2C)  {consumer.email}")

    db.commit()
    print(f"\nListo. {len(users) + len(consumers)} cuenta(s) actualizada(s).")
    return 0


def _reset(db, email: str, is_consumer: bool, password: str | None = None) -> int:
    email = email.lower().strip()

    if is_consumer:
        account = db.query(Consumer).filter(Consumer.email == email).first()
        etiqueta = "cliente"
    else:
        account = db.query(User).filter(User.email == email).first()
        etiqueta = "usuario de negocio"

    if account is None:
        print(f"No existe ningún {etiqueta} con el email '{email}'.")
        print("Corre el script con --list para ver las cuentas disponibles.")
        return 1

    if not account.is_active:
        print(f"AVISO: la cuenta '{email}' está marcada como inactiva; no podrá iniciar sesión")
        print("aunque cambies la contraseña.")

    if password is None:
        password = _prompt_password()
    nuevo_hash = hash_password(password)

    if is_consumer:
        account.hashed_password = nuevo_hash
    else:
        account.password_hash = nuevo_hash

    db.commit()
    print(f"\nListo. Contraseña actualizada para {etiqueta} '{email}'.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Restablece la contraseña de una cuenta de KARD.")
    parser.add_argument("email", nargs="?", help="Email de la cuenta a modificar")
    parser.add_argument("--list", action="store_true", help="Listar todas las cuentas y salir")
    parser.add_argument("--consumer", action="store_true", help="Buscar en clientes (B2C) en vez de usuarios de negocio")
    parser.add_argument("--all", action="store_true", help="Aplicar la misma contraseña a TODAS las cuentas")
    parser.add_argument("--password", help="Contraseña sin prompt — solo para desarrollo (queda en el historial del shell)")
    args = parser.parse_args()

    if not args.list and not args.email and not args.all:
        parser.error("Indica un email, o usa --all para todas, o --list para ver las cuentas existentes.")

    if args.password:
        error = _validate(args.password)
        if error:
            parser.error(error)

    db = SessionLocal()
    try:
        if args.list:
            _list_accounts(db)
            return 0
        if args.all:
            password = args.password or _prompt_password()
            return _reset_all(db, password)
        return _reset(db, args.email, args.consumer, args.password)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
