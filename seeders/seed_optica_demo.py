"""
Seeder — demo completo para tenant óptica.

Crea (si no existe) un tenant optician de demostración con su usuario
business_admin, lo marca como público, y siembra 5 lentes terminados
(product_type = 'final') con datos de prescripción realistas.

Uso:
    python seeders/seed_optica_demo.py

El script es idempotente:
  - Si ya existe un tenant optician activo, siembra los productos en él.
  - Si un producto con el mismo nombre ya existe, lo omite.
  - No crea duplicados de tenant ni de usuario.
"""

import re
import sys
import os
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import SessionLocal
from db.models import Product, Tenant, User
from core.security import hash_password
from utils.helpers import generate_id


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:55]


def _unique_slug(db, base: str) -> str:
    if not db.query(Tenant).filter(Tenant.slug == base).first():
        return base
    for n in range(2, 10_000):
        candidate = f"{base}-{n}"
        if not db.query(Tenant).filter(Tenant.slug == candidate).first():
            return candidate
    raise RuntimeError(f"No se pudo generar slug único desde '{base}'")

# ---------------------------------------------------------------------------
# Datos del tenant demo — usados solo si no existe ningún optician activo
# ---------------------------------------------------------------------------
DEMO_TENANT = {
    "business_name": "Óptica Visión Total",
    "email":         "admin@optica-vision-total.com",
    "slug":          "optica-vision-total",
    "password":      "Demo1234!",   # solo para desarrollo / testing
}

# ---------------------------------------------------------------------------
# Catálogo — 5 lentes terminados (product_type = 'final')
# Metadata sigue OPTICIAN_SCHEMA: esfera, cilindro, eje, material
# ---------------------------------------------------------------------------
PRODUCTOS: list[tuple] = [
    # (nombre, descripcion, precio, stock, umbral, metadata)
    (
        "Lente Monofocal CR-39",
        "Lente orgánico estándar para miopía o hipermetropía leve. "
        "Liviano, económico y de alta claridad óptica.",
        350.00, 15, 3,
        {"esfera": "-2.50", "cilindro": "0.00", "eje": 0, "material": "CR-39"},
    ),
    (
        "Lente Monofocal Policarbonato",
        "Alta resistencia a impactos, ideal para niños y actividades deportivas. "
        "Incluye tratamiento UV integrado.",
        550.00, 10, 3,
        {"esfera": "+1.25", "cilindro": "-0.50", "eje": 90, "material": "Policarbonato"},
    ),
    (
        "Lente Bifocal CR-39",
        "Corrección para visión lejana y cercana en un solo lente. "
        "Línea de segmento visible, transición limpia.",
        750.00, 8, 2,
        {"esfera": "-3.00", "cilindro": "-1.00", "eje": 45, "material": "CR-39"},
    ),
    (
        "Lente Progresivo Trivex",
        "Progresivo premium sin línea visible. Material Trivex: "
        "el más ligero del mercado con excelente resistencia al impacto.",
        1_200.00, 5, 2,
        {"esfera": "+2.00", "cilindro": "-0.75", "eje": 135, "material": "Trivex"},
    ),
    (
        "Lente Alto Índice 1.67",
        "Para prescripciones altas (> ±4.00). "
        "Hasta 50 % más delgado que CR-39, acabado antirreflejante incluido.",
        1_800.00, 5, 2,
        {"esfera": "-5.00", "cilindro": "-1.50", "eje": 80, "material": "Alto Índice 1.67"},
    ),
]


def _get_or_create_tenant(db) -> tuple[Tenant, str]:
    """
    Retorna (tenant, created_by_user_id).

    Si existe al menos un tenant optician activo, usa el primero y
    recupera su usuario admin para el campo created_by del producto.
    Si no existe ninguno, crea el tenant demo completo.
    """
    tenant = (
        db.query(Tenant)
        .filter(Tenant.business_type == "optician", Tenant.is_active.is_(True))
        .first()
    )

    if tenant:
        user = (
            db.query(User)
            .filter(User.tenant_id == tenant.id, User.role == "business_admin")
            .first()
        )
        print(f"Tenant existente: {tenant.business_name} ({tenant.id})")

        if not tenant.slug:
            tenant.slug = _unique_slug(db, _slugify(tenant.business_name))
            print(f"  → slug asignado: {tenant.slug}")

        return tenant, (user.id if user else None)

    # Verificar que el email demo no esté en uso
    email = DEMO_TENANT["email"]
    if db.query(Tenant).filter(Tenant.email == email).first():
        print(f"ADVERTENCIA: el email demo '{email}' ya está en uso por otro tenant.")
        print("Edita DEMO_TENANT['email'] en el seeder para evitar el conflicto.")
        sys.exit(1)

    tenant = Tenant(
        id=generate_id("t"),
        business_name=DEMO_TENANT["business_name"],
        business_type="optician",
        email=email,
        slug=DEMO_TENANT["slug"],
        is_public=True,   # visible en storefront inmediatamente
        is_active=True,
    )
    db.add(tenant)
    db.flush()  # obtiene el id sin commitear aún

    user = User(
        id=generate_id("u"),
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(DEMO_TENANT["password"]),
        role="business_admin",
        is_active=True,
    )
    db.add(user)
    db.flush()

    print(f"Tenant creado: {tenant.business_name} ({tenant.id})")
    print(f"  slug    : {tenant.slug}")
    print(f"  email   : {tenant.email}")
    print(f"  password: {DEMO_TENANT['password']}  ← solo desarrollo")
    return tenant, user.id


def seed() -> None:
    db = SessionLocal()
    try:
        tenant, created_by = _get_or_create_tenant(db)

        # Asegurar que el storefront esté público
        if not tenant.is_public:
            tenant.is_public = True
            print(f"  → is_public actualizado a True")

        created = 0
        skipped = 0
        for (nombre, desc, precio, stock, umbral, meta) in PRODUCTOS:
            existe = (
                db.query(Product)
                .filter(Product.tenant_id == tenant.id, Product.name == nombre)
                .first()
            )
            if existe:
                skipped += 1
                continue

            db.add(Product(
                id=generate_id("p", 20),
                tenant_id=tenant.id,
                product_type="final",
                name=nombre,
                description=desc,
                price=precio,
                inventory_count=stock,
                low_stock_threshold=umbral,
                metadata_=meta,
                created_by=created_by,
            ))
            created += 1

        db.commit()
        print(f"\nProductos creados : {created}")
        print(f"Productos omitidos: {skipped} (ya existían)")
        print(f"\nStorefront disponible en: /s/{tenant.slug}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
