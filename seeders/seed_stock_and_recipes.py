"""
Seeder — stock parejo + recetas de prueba.

Deja el catálogo listo para probar el descuento de inventario en cascada:

  - Todos los productos FINAL  → inventory_count = FINAL_STOCK  (default 20)
  - Todos los productos INSUMO → inventory_count = INSUMO_STOCK (default 100)
  - Crea/actualiza las RECETAS de los platillos de restaurante, enlazando
    cada platillo con sus insumos y una cantidad entera por unidad vendida.

Es idempotente y re-ejecutable: siempre deja los mismos valores absolutos,
así puedes correrlo, vender, y volver a correrlo para resetear y probar.

Uso (dentro del contenedor):
    docker exec kard-backend-1 python seeders/seed_stock_and_recipes.py
"""

import os
import sys

try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
except NameError:
    pass  # ejecutado vía stdin (docker exec ... python -); el CWD ya está en el path

from db.database import SessionLocal
from db.models import Product, ProductRecipe, Tenant
from utils.helpers import generate_id

# ---------------------------------------------------------------------------
# Parámetros de stock para pruebas
# ---------------------------------------------------------------------------
FINAL_STOCK = 20     # lentes, platillos (por si venden "por pedido" o con stock)
INSUMO_STOCK = 100   # ingredientes — holgado para ver el descuento

# ---------------------------------------------------------------------------
# Recetas por nombre de platillo → { nombre_insumo: cantidad_por_unidad }
# Solo se aplican a tenants de tipo restaurant. Las cantidades son enteras
# (unidades del stock del insumo consumidas por cada platillo vendido).
# ---------------------------------------------------------------------------
RECIPES: dict[str, dict[str, int]] = {
    "Chilaquiles": {
        "Tomate verde": 2,
        "Cebolla blanca": 1,
        "Chile jalapeño": 1,
        "Maíz para tortilla": 2,
        "Crema ácida": 1,
        "Queso fresco": 1,
        "Pollo entero": 1,
    },
    "Enchiladas Verdes": {
        "Tomate verde": 3,
        "Maíz para tortilla": 3,
        "Pollo entero": 1,
        "Crema ácida": 1,
        "Queso Oaxaca": 1,
        "Cebolla blanca": 1,
    },
    "Enchiladas rojas": {
        "Chile guajillo": 1,
        "Chile ancho seco": 1,
        "Maíz para tortilla": 3,
        "Pollo entero": 1,
        "Crema ácida": 1,
        "Queso fresco": 1,
        "Cebolla blanca": 1,
    },
    "Lasaña": {
        "Carne de res": 2,
        "Tomate rojo": 2,
        "Cebolla blanca": 1,
        "Ajo": 1,
        "Queso Oaxaca": 2,
        "Aceite vegetal": 1,
    },
}


def seed() -> None:
    db = SessionLocal()
    try:
        # 1. Stock parejo en todos los productos activos
        finals = 0
        insumos = 0
        for p in db.query(Product).filter(Product.is_deleted.is_(False)).all():
            if p.product_type == "final":
                p.inventory_count = FINAL_STOCK
                finals += 1
            elif p.product_type == "insumo":
                p.inventory_count = INSUMO_STOCK
                insumos += 1
        db.flush()
        print(f"Stock actualizado → {finals} finales={FINAL_STOCK}, {insumos} insumos={INSUMO_STOCK}")

        # 2. Recetas para cada tenant restaurant
        recipes_created = 0
        recipes_updated = 0
        skipped = []

        restaurants = db.query(Tenant).filter(Tenant.business_type == "restaurant").all()
        for tenant in restaurants:
            # Índice nombre→producto para este tenant (no eliminados)
            by_name = {
                p.name: p
                for p in db.query(Product)
                .filter(Product.tenant_id == tenant.id, Product.is_deleted.is_(False))
                .all()
            }

            for dish_name, ingredients in RECIPES.items():
                dish = by_name.get(dish_name)
                if dish is None or dish.product_type != "final":
                    continue  # este restaurante no tiene ese platillo

                for insumo_name, qty in ingredients.items():
                    insumo = by_name.get(insumo_name)
                    if insumo is None or insumo.product_type != "insumo":
                        skipped.append(f"{tenant.business_name}: falta insumo '{insumo_name}' para '{dish_name}'")
                        continue

                    existing = (
                        db.query(ProductRecipe)
                        .filter(
                            ProductRecipe.tenant_id == tenant.id,
                            ProductRecipe.final_product_id == dish.id,
                            ProductRecipe.insumo_product_id == insumo.id,
                        )
                        .first()
                    )
                    if existing is not None:
                        if existing.quantity != qty:
                            existing.quantity = qty
                            recipes_updated += 1
                    else:
                        db.add(ProductRecipe(
                            id=generate_id("rec", 20),
                            tenant_id=tenant.id,
                            final_product_id=dish.id,
                            insumo_product_id=insumo.id,
                            quantity=qty,
                        ))
                        recipes_created += 1

        db.commit()
        print(f"Recetas → {recipes_created} creadas, {recipes_updated} actualizadas")
        if skipped:
            print("Avisos (insumos no encontrados):")
            for s in skipped:
                print(f"  - {s}")
        print("\nListo. Vende un platillo y verás bajar su stock y el de sus insumos.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
