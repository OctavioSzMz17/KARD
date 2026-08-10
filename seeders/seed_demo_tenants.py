"""
Seeder — dos tenants demo desde cero (restaurante + óptica).

Crea, para cada uno:
  - el tenant, público y con slug propio
  - su usuario business_admin (mismo password, email distinto)
  - la personalización completa: tema de colores, logo y banner (branding)
    más tagline, descripción y datos de contacto (profile)
  - un catálogo inicial de productos `final`

La personalización se guarda en `Tenant.config` (JSONB) con exactamente la
misma forma que escribe `PUT /api/v1/settings/branding`, así que el panel de
customización la lee y la edita sin ningún paso extra.

Uso (dentro del contenedor):
    docker exec kard-backend-1 python seeders/seed_demo_tenants.py

Es idempotente: si el email ya existe reutiliza ese tenant y solo refresca
branding/profile; los productos con nombre repetido se omiten.

Para completar el restaurante (insumos + recetas) corre después:
    docker exec kard-backend-1 python seeders/seed_restaurant_insumos.py
    docker exec kard-backend-1 python seeders/seed_stock_and_recipes.py
"""

import os
import re
import sys
import unicodedata

try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
except NameError:
    pass  # ejecutado vía stdin; el CWD ya está en el path

from core.security import hash_password
from db.database import SessionLocal
from db.models import Product, Tenant, User
from utils.helpers import generate_id

# Misma contraseña para todos los admins demo — solo desarrollo.
PASSWORD = "testpass123!"

# ---------------------------------------------------------------------------
# Definición de los tenants demo.
#
# `branding` y `profile` respetan el contrato que consume el frontend
# (ver settings/models.py): los colores son hex #RRGGBB y las URLs deben
# empezar con http:// o https://.
# ---------------------------------------------------------------------------
TENANTS: list[dict] = [
    {
        "business_name": "Sabor Norteño",
        "business_type": "restaurant",
        "email": "admin@sabor-norteno.com",
        "phone": "+52 81 1234 5678",
        "branding": {
            "logo_url": "https://placehold.co/240x240/b91c1c/ffffff?text=Sabor+Norte%C3%B1o",
            "banner_url": "https://placehold.co/1200x400/7f1d1d/fbbf24?text=Cocina+del+Norte",
            "theme": {"primary": "#b91c1c", "accent": "#f59e0b"},
        },
        "profile": {
            "tagline": "Cocina norteña de leña, como en casa",
            "description": (
                "Restaurante familiar especializado en cortes al carbón, "
                "machaca y antojitos regionales. Ingredientes frescos del día "
                "y recetas que llevan tres generaciones en la familia."
            ),
            "contact": {
                "address": "Av. Constitución 1450, Centro, Monterrey, N.L.",
                "phone": "+52 81 1234 5678",
                "email": "hola@sabor-norteno.com",
                "whatsapp": "+52 81 1234 5678",
                "maps_url": "https://maps.google.com/?q=Monterrey+Centro",
                "hours": [
                    {"days": "Lunes a Viernes", "hours": "08:00 – 22:00"},
                    {"days": "Sábado y Domingo", "hours": "09:00 – 23:00"},
                ],
                "social": {
                    "facebook": "https://facebook.com/sabornorteno",
                    "instagram": "https://instagram.com/sabornorteno",
                    "tiktok": None,
                    "x": None,
                    "youtube": None,
                },
            },
        },
        # (nombre, descripcion, precio, stock, umbral, metadata)
        "productos": [
            (
                "Tacos de Carne Asada (orden)",
                "Cuatro tacos de arrachera al carbón con cebollita y limón.",
                145.00, 25, 5,
                {"categoria": "plato_fuerte", "tiempo_preparacion_min": 15, "picante": False},
            ),
            (
                "Enchiladas Verdes",
                "Tortillas rellenas de pollo bañadas en salsa verde, crema y queso.",
                120.00, 20, 5,
                {"categoria": "plato_fuerte", "tiempo_preparacion_min": 20, "picante": True,
                 "alergenos": ["lácteos", "gluten"]},
            ),
            (
                "Guacamole con Totopos",
                "Aguacate molcajeteado al momento con cilantro, cebolla y limón.",
                85.00, 30, 6,
                {"categoria": "entrada", "tiempo_preparacion_min": 10, "picante": False},
            ),
            (
                "Arroz a la Mexicana",
                "Arroz rojo con verduras salteadas. Guarnición clásica.",
                45.00, 40, 8,
                {"categoria": "guarnicion", "tiempo_preparacion_min": 12, "picante": False},
            ),
            (
                "Flan Napolitano",
                "Flan casero de vainilla con caramelo natural.",
                65.00, 15, 4,
                {"categoria": "postre", "tiempo_preparacion_min": 5, "picante": False,
                 "alergenos": ["lácteos", "huevo"]},
            ),
            (
                "Agua de Horchata (1 L)",
                "Preparada al día con arroz, canela y vainilla.",
                40.00, 35, 8,
                {"categoria": "bebida", "tiempo_preparacion_min": 5, "picante": False},
            ),
        ],
    },
    {
        "business_name": "Óptica Claridad",
        "business_type": "optician",
        "email": "admin@optica-claridad.com",
        "phone": "+52 55 9876 5432",
        "branding": {
            "logo_url": "https://placehold.co/240x240/0e7490/ffffff?text=Claridad",
            "banner_url": "https://placehold.co/1200x400/164e63/67e8f9?text=Ve+el+mundo+claro",
            "theme": {"primary": "#0e7490", "accent": "#22d3ee"},
        },
        "profile": {
            "tagline": "Tu vista en las mejores manos",
            "description": (
                "Óptica con más de 15 años graduando la vista de familias. "
                "Examen computarizado sin costo con la compra de tus lentes y "
                "garantía de adaptación de 30 días."
            ),
            "contact": {
                "address": "Insurgentes Sur 890, Del Valle, CDMX",
                "phone": "+52 55 9876 5432",
                "email": "contacto@optica-claridad.com",
                "whatsapp": "+52 55 9876 5432",
                "maps_url": "https://maps.google.com/?q=Del+Valle+CDMX",
                "hours": [
                    {"days": "Lunes a Viernes", "hours": "10:00 – 19:00"},
                    {"days": "Sábado", "hours": "10:00 – 15:00"},
                ],
                "social": {
                    "facebook": "https://facebook.com/opticaclaridad",
                    "instagram": "https://instagram.com/opticaclaridad",
                    "tiktok": None,
                    "x": None,
                    "youtube": None,
                },
            },
        },
        "productos": [
            (
                "Lente Monofocal CR-39",
                "Lente orgánico estándar para miopía o hipermetropía leve.",
                350.00, 20, 5,
                {"esfera": "-2.50", "cilindro": "0.00", "eje": 0, "material": "CR-39"},
            ),
            (
                "Lente Monofocal Policarbonato",
                "Alta resistencia al impacto, ideal para niños y deporte.",
                550.00, 15, 4,
                {"esfera": "+1.25", "cilindro": "-0.50", "eje": 90, "material": "Policarbonato"},
            ),
            (
                "Lente Antirreflejante Premium",
                "Tratamiento antirreflejo y filtro de luz azul para pantallas.",
                890.00, 12, 3,
                {"esfera": "-1.75", "cilindro": "-0.25", "eje": 180, "material": "CR-39 AR"},
            ),
            (
                "Lente Progresivo Trivex",
                "Progresivo sin línea visible, el material más ligero del mercado.",
                1_200.00, 8, 2,
                {"esfera": "+2.00", "cilindro": "-0.75", "eje": 135, "material": "Trivex"},
            ),
            (
                "Lente Fotocromático",
                "Se oscurece con la luz solar y aclara en interiores.",
                1_450.00, 10, 3,
                {"esfera": "-3.25", "cilindro": "-1.00", "eje": 45, "material": "Fotocromático"},
            ),
        ],
    },
]


def _slugify(text: str) -> str:
    """'Óptica Claridad' → 'optica-claridad'."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:55]


def _unique_slug(db, base: str, tenant_id: str | None = None) -> str:
    """Slug libre, agregando -2, -3... si ya está tomado por OTRO tenant."""
    def taken(candidate: str) -> bool:
        row = db.query(Tenant).filter(Tenant.slug == candidate).first()
        return row is not None and row.id != tenant_id

    if not taken(base):
        return base
    for n in range(2, 10_000):
        if not taken(f"{base}-{n}"):
            return f"{base}-{n}"
    raise RuntimeError(f"No se pudo generar un slug único desde '{base}'")


def _upsert_tenant(db, spec: dict) -> tuple[Tenant, str]:
    """Crea el tenant + su admin, o reutiliza los existentes si el email ya está."""
    email = spec["email"].lower()
    tenant = db.query(Tenant).filter(Tenant.email == email).first()

    if tenant is None:
        tenant = Tenant(
            id=generate_id("t"),
            business_name=spec["business_name"],
            business_type=spec["business_type"],
            email=email,
            phone=spec.get("phone"),
            slug=_unique_slug(db, _slugify(spec["business_name"])),
            is_public=True,
            is_active=True,
            config={},
        )
        db.add(tenant)
        db.flush()
        print(f"Tenant creado : {tenant.business_name} ({tenant.id})")
    else:
        print(f"Tenant existente: {tenant.business_name} ({tenant.id})")
        if not tenant.slug:
            tenant.slug = _unique_slug(db, _slugify(tenant.business_name), tenant.id)
        tenant.is_public = True
        tenant.is_active = True

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            id=generate_id("u"),
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(PASSWORD),
            role="business_admin",
            is_active=True,
        )
        db.add(user)
        db.flush()
        print(f"  admin creado : {email}")
    else:
        # Re-ejecutar el seeder deja la contraseña en un estado conocido.
        user.password_hash = hash_password(PASSWORD)
        user.is_active = True
        print(f"  admin existente: {email} (contraseña restablecida)")

    return tenant, user.id


def _apply_customization(tenant: Tenant, spec: dict) -> None:
    """
    Escribe branding y profile en el JSONB `config`.

    Se reasigna el dict completo (en vez de mutarlo) porque SQLAlchemy solo
    detecta cambios en una columna JSONB cuando el objeto cambia de identidad.
    """
    config = dict(tenant.config or {})
    config["branding"] = spec["branding"]
    config["profile"] = spec["profile"]
    tenant.config = config
    theme = spec["branding"]["theme"]
    print(f"  branding     : primary={theme['primary']} accent={theme['accent']} + logo y banner")


def _seed_products(db, tenant: Tenant, created_by: str, productos: list[tuple]) -> tuple[int, int]:
    """Agrega los productos `final` que aún no existan en el tenant."""
    created = skipped = 0
    for (nombre, desc, precio, stock, umbral, metadata) in productos:
        exists = (
            db.query(Product)
            .filter(Product.tenant_id == tenant.id, Product.name == nombre)
            .first()
        )
        if exists:
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
            metadata_=metadata,
            created_by=created_by,
        ))
        created += 1
    return created, skipped


def seed() -> None:
    db = SessionLocal()
    try:
        resumen = []
        for spec in TENANTS:
            print(f"\n--- {spec['business_name']} ({spec['business_type']}) ---")
            tenant, user_id = _upsert_tenant(db, spec)
            _apply_customization(tenant, spec)
            created, skipped = _seed_products(db, tenant, user_id, spec["productos"])
            print(f"  productos    : {created} creados, {skipped} omitidos")
            resumen.append((tenant, spec["email"]))

        db.commit()

        print("\n" + "=" * 62)
        print("LISTO — credenciales de acceso (solo desarrollo)")
        print("=" * 62)
        for tenant, email in resumen:
            print(f"\n  {tenant.business_name}  [{tenant.business_type}]")
            print(f"    email      : {email}")
            print(f"    password   : {PASSWORD}")
            print(f"    storefront : /s/{tenant.slug}")
        print()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
