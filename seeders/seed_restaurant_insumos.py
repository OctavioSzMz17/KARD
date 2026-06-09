"""
Seeder — insumos de cocina para tenants restaurant.

Carga un catálogo base de ingredientes/supplies comunes en un
restaurante mexicano. Diseñado para poblar la sección "Inventario"
del sidebar (product_type = 'insumo'), separada de los platillos
terminados que viven en "Productos" (product_type = 'final').

Uso:
    python seeders/seed_restaurant_insumos.py

El script es idempotente: si un insumo con el mismo nombre ya existe
en el tenant lo omite (no lanza error ni crea duplicados).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import SessionLocal
from db.models import Product, Tenant
from utils.helpers import generate_id

# ---------------------------------------------------------------------------
# Catálogo de insumos — formato: (nombre, descripcion, precio, stock,
# umbral_alerta, unidad_medida, perecedero, dias_expiracion)
# ---------------------------------------------------------------------------
INSUMOS = [
    # Carnes
    ("Pollo entero",        "Pollo fresco para guisar",                 85.00,  20, 5,  "kg",     True,  3),
    ("Carne de res",        "Corte para guisados y caldos",             120.00, 15, 4,  "kg",     True,  4),
    ("Carne de cerdo",      "Para carnitas y guisos",                   95.00,  15, 4,  "kg",     True,  4),
    ("Chorizo",             "Chorizo rojo estilo mexicano",             55.00,  10, 3,  "kg",     True,  7),
    # Frutas y verduras
    ("Tomate rojo",         "Tomate para salsas y guisos",              18.00,  30, 8,  "kg",     True,  7),
    ("Tomate verde",        "Tomatillo para salsas verdes",             20.00,  25, 6,  "kg",     True,  7),
    ("Cebolla blanca",      "Cebolla para base de guisos",              15.00,  20, 5,  "kg",     True,  14),
    ("Chile jalapeño",      "Chile fresco para salsas y garnachas",     22.00,  15, 4,  "kg",     True,  10),
    ("Chile ancho seco",    "Chile seco para moles y adobos",           60.00,  8,  2,  "kg",     True,  180),
    ("Chile guajillo",      "Chile seco para salsas y caldos",          55.00,  8,  2,  "kg",     True,  180),
    ("Ajo",                 "Cabezas de ajo fresco",                    30.00,  10, 3,  "kg",     True,  30),
    ("Limón",               "Limón mexicano",                           12.00,  40, 10, "kg",     True,  10),
    ("Aguacate",            "Aguacate Hass maduro",                     25.00,  30, 8,  "pieza",  True,  4),
    ("Cilantro",            "Manojo de cilantro fresco",                8.00,   20, 5,  "manojo", True,  5),
    ("Epazote",             "Hierba para frijoles y caldos",            6.00,   15, 4,  "manojo", True,  5),
    # Granos y secos
    ("Frijol negro",        "Frijol negro seco para cocinar",           28.00,  25, 6,  "kg",     False, 0),
    ("Arroz",               "Arroz blanco de grano largo",              22.00,  30, 8,  "kg",     False, 0),
    ("Maíz para tortilla",  "Masa de maíz para tortillas frescas",      18.00,  20, 5,  "kg",     True,  3),
    # Lácteos y huevo
    ("Huevo",               "Huevo blanco por kilo",                    42.00,  20, 5,  "kg",     True,  21),
    ("Crema ácida",         "Crema para enchiladas y tacos",            38.00,  10, 3,  "litro",  True,  14),
    ("Queso Oaxaca",        "Queso para quesadillas y enchiladas",      90.00,  10, 3,  "kg",     True,  14),
    ("Queso fresco",        "Queso para desmoronar en platillos",       70.00,  10, 3,  "kg",     True,  10),
    # Aceites y condimentos
    ("Aceite vegetal",      "Aceite para freír y aderezar",             48.00,  12, 3,  "litro",  False, 0),
    ("Sal de mesa",         "Sal fina para sazón",                      8.00,   10, 2,  "kg",     False, 0),
    ("Azúcar",              "Azúcar blanca refinada",                   22.00,  8,  2,  "kg",     False, 0),
    ("Consomé de pollo",    "Consomé en polvo para sazón",              35.00,  6,  2,  "kg",     False, 0),
]


def seed(tenant_id: str, created_by: str) -> None:
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            print(f"ERROR: tenant '{tenant_id}' no encontrado")
            return
        if tenant.business_type != "restaurant":
            print(f"ERROR: tenant '{tenant_id}' es '{tenant.business_type}', no 'restaurant'")
            return

        created = 0
        skipped = 0
        for (nombre, desc, precio, stock, umbral, unidad, perecedero, dias) in INSUMOS:
            exists = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.name == nombre,
            ).first()
            if exists:
                skipped += 1
                continue

            db.add(Product(
                id=generate_id("p"),
                tenant_id=tenant_id,
                product_type="insumo",
                name=nombre,
                description=desc,
                price=precio,
                inventory_count=stock,
                low_stock_threshold=umbral,
                metadata_={
                    "unidad_medida": unidad,
                    "perecedero": perecedero,
                    "dias_expiracion": dias,
                },
                created_by=created_by,
            ))
            created += 1

        db.commit()
        print(f"Tenant: {tenant.business_name} ({tenant_id})")
        print(f"  Creados : {created}")
        print(f"  Omitidos: {skipped} (ya existían)")
    finally:
        db.close()


if __name__ == "__main__":
    from db.database import SessionLocal
    from db.models import Tenant, User

    db = SessionLocal()
    tenants = db.query(Tenant).filter(Tenant.business_type == "restaurant", Tenant.is_active == True).all()
    admin = db.query(User).filter(User.role == "saas_admin").first()
    db.close()

    if not tenants:
        print("No hay tenants restaurant activos.")
        sys.exit(1)

    print(f"Seedeando insumos en {len(tenants)} tenant(s) restaurant...\n")
    for t in tenants:
        seed(t.id, admin.id if admin else None)
