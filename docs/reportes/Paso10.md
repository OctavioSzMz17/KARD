# Reporte de Implementación — PASO 10: Recetas + Descuento en Cascada
**Proyecto:** KARD
**Fase:** MVP Fase 1 — Plataforma B2B2C
**Paso:** 10 — Recetas (Bill of Materials) e inventario en cascada
**Fecha:** 2026-07-11
**Estado:** ✅ Implementado y probado en vivo

---

## Resumen

Un producto **final** (platillo) ahora puede tener una **receta**: la lista de **insumos** que consume al venderse. Al confirmar un pedido, además de descontar el stock del platillo, se descuenta el de cada insumo (`cantidad_receta × unidades_vendidas`), todo en la misma transacción atómica.

Además, el checkout ahora tiene un **flujo de confirmación**: si algo no tiene stock, no bloquea a ciegas — devuelve un `409` estructurado para que el front pregunte al usuario si continuar sin esos productos o cancelar.

---

## 1. Gestión de recetas (B2B — dashboard del negocio)

Requiere 🔒 JWT de negocio. Montado bajo `/api/v1/products`.

### `GET /api/v1/products/{product_id}/recipe`
Ver la receta de un producto final.

**Response `200`:**
```json
{
  "final_product_id": "p_lasana",
  "final_product_name": "Lasaña",
  "items": [
    { "insumo_id": "p_carne", "insumo_name": "Carne de res", "quantity": 2, "unit": "kg" },
    { "insumo_id": "p_queso", "insumo_name": "Queso Oaxaca", "quantity": 2, "unit": "kg" }
  ]
}
```

### `POST /api/v1/products/{product_id}/recipe/items`
Agregar un ingrediente (o actualizar su cantidad si ya existe — upsert).

**Request:**
```json
{ "insumo_product_id": "p_carne", "quantity": 2 }
```

Devuelve la receta completa actualizada (mismo schema que el GET).

**Validaciones:**
- El producto padre debe ser `product_type = "final"`.
- El ingrediente debe ser `product_type = "insumo"`.
- Ambos del mismo tenant (aislamiento).
- No auto-referencia (un producto no puede ser ingrediente de sí mismo).
- `quantity` entero > 0.

### `DELETE /api/v1/products/{product_id}/recipe/items/{insumo_id}`
Quita un ingrediente. Devuelve la receta actualizada.

**Errores:**
| Status | Causa |
|---|---|
| `400` | El padre no es `final`, el ingrediente no es `insumo`, o auto-referencia |
| `404` | Producto o ingrediente no existe en este tenant / no está en la receta |

---

## 2. Descuento en cascada (checkout B2C)

En `POST /api/v1/orders`, para cada producto del carrito:

```
1. SELECT FOR UPDATE del producto final (bloqueo de fila).
2. Carga su receta.
3. SELECT FOR UPDATE de cada insumo de la receta.
4. Valida stock: del final Y de cada insumo (cantidad × unidades).
5. Descuenta el final + cada insumo.
6. Registra un inventory_transaction por cada movimiento (auditoría).
```

Un producto sin receta se comporta como antes: solo descuenta su propio stock.

### Decisión de diseño — cantidades enteras
La `quantity` de la receta es **entera** (unidades del stock del insumo por unidad vendida), para mantener consistencia con el `inventory_count` entero y el CHECK de `inventory_transactions`. Si un negocio necesita fracciones (ej. 200 g), define el stock del insumo en esa unidad menor (ej. stock en "porciones de 100g").

---

## 3. Flujo de confirmación de stock

`POST /api/v1/orders` acepta `on_insufficient_stock`:

| Valor | Comportamiento |
|---|---|
| `"reject"` (default) | Si falta stock → `409` estructurado, no crea nada, carrito intacto |
| `"skip"` | Omite los productos sin stock, crea el pedido con el resto, los reporta en `skipped` |

**409 estructurado** (para que el front lo interprete):
```json
{
  "success": false,
  "code": "INSUFFICIENT_STOCK",
  "error": "Algunos productos no tienen stock suficiente",
  "details": {
    "all_unavailable": false,
    "unavailable": [
      {
        "product_id": "p_tacos",
        "product_name": "Tacos al pastor",
        "requested": 3,
        "reason": "ingredient_shortage",
        "missing_ingredients": [
          { "insumo_id": "p_carne", "insumo_name": "Carne de cerdo",
            "required": 6, "available": 2, "unit": "kg" }
        ]
      }
    ]
  }
}
```

`reason`: `product_shortage` | `ingredient_shortage` | `product_and_ingredient` | `unavailable`.

El detalle completo del flujo para el front está en [`API_Frontend_Cliente.md`](API_Frontend_Cliente.md) §5.3.

---

## 4. Prueba en vivo (verificada)

Venta de **2 Lasañas** (receta: Carne de res ×2, Tomate rojo ×2, Cebolla blanca ×1, Ajo ×1, Queso Oaxaca ×2, Aceite vegetal ×1):

| Producto | Antes | Después |
|---|---|---|
| Lasaña (final) | 20 | **18** |
| Carne de res | 100 | **96** |
| Tomate rojo | 100 | **96** |
| Cebolla blanca | 100 | **98** |
| Ajo | 100 | **98** |
| Queso Oaxaca | 100 | **96** |
| Aceite vegetal | 100 | **98** |

Pedido creado con `status=pending`, todo en una transacción atómica. ✅

---

## 5. Seeder de pruebas

[`seeders/seed_stock_and_recipes.py`](../../seeders/seed_stock_and_recipes.py) — re-ejecutable:
- Finales → stock 20, insumos → stock 100.
- Crea las recetas de los 4 platillos del restaurante.

```bash
docker exec kard-backend-1 python seeders/seed_stock_and_recipes.py
```

Corre → vende → vuelve a correr para resetear y probar de nuevo.

---

## 6. Archivos

```
recipes/
├── __init__.py
├── models.py      ← RecipeItemCreate, RecipeItemResponse, RecipeResponse
├── repository.py  ← RecipeRepository
├── service.py     ← RecipeService (validaciones + upsert)
└── routes.py      ← GET/POST/DELETE recipe

migrations/004_product_recipes.sql   ← tabla product_recipes (ya aplicada en Neon)
seeders/seed_stock_and_recipes.py    ← stock parejo + recetas
```

| Archivo modificado | Cambio |
|---|---|
| `db/models.py` | `+ ProductRecipe` |
| `core/exceptions.py` | `+ InvalidRecipeError`, `+ OrderStockConflictError` |
| `orders/models.py` | `+ on_insufficient_stock`, `+ SkippedItemResponse`, `+ skipped` |
| `orders/service.py` | Análisis en 2 fases + cascada de insumos + flujo reject/skip |
| `orders/routes.py` | Pasa `on_insufficient_stock` |
| `main.py` | Handlers 400/409 + router de recipes |

---

## Pendiente / decisiones abiertas

- El endpoint B2B `POST /products/{id}/sell` (ajuste manual de stock) **no** hace cascada de ingredientes — solo el checkout B2C. Se puede agregar si se quiere que la venta manual del negocio también descuente insumos.
- Alertas de stock bajo por email cuando un **insumo** cruza su umbral tras una venta (hoy la alerta existe pero no se dispara automáticamente en el checkout).
