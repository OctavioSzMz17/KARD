# Reporte de Implementación — PASO 8: Explore / Discovery
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 8 — Descubrimiento de Tiendas  
**Fecha:** 2026-07-04  
**Estado:** ✅ Implementado y verificado

---

## Resumen

El módulo `explore/` expone dos endpoints públicos que permiten al cliente navegar la plataforma sin conocer ningún slug de antemano. Es la entrada al marketplace: el cliente ve las categorías disponibles, filtra por tipo de negocio, busca por nombre, y aterriza en la tienda que le interesa.

---

## Endpoints

**Prefijo:** `/api/v1/explore`

### `GET /api/v1/explore/categories`
Devuelve los tipos de negocio que tienen al menos una tienda pública activa, ordenados por cantidad de tiendas (mayor primero).

**Response `200 OK`:**
```json
{
  "categories": [
    { "business_type": "restaurant", "store_count": 3 },
    { "business_type": "optician",   "store_count": 1 }
  ]
}
```

> El tipo `"platform"` (tenant interno de KARD) nunca aparece — está excluido en el repositorio a nivel de query.

---

### `GET /api/v1/explore`
Lista paginada de tiendas públicas. Acepta filtros opcionales por categoría y búsqueda de nombre.

**Query params:**

| Parámetro | Tipo | Default | Descripción |
|---|---|---|---|
| `category` | string | — | Filtra por `business_type` exacto (`restaurant`, `optician`) |
| `search` | string | — | Búsqueda parcial en `business_name` (case-insensitive) |
| `page` | int | `1` | Página (≥1) |
| `page_size` | int | `20` | Items por página (1–100) |

**Ejemplos:**
```
GET /api/v1/explore
GET /api/v1/explore?category=restaurant
GET /api/v1/explore?search=optica
GET /api/v1/explore?category=restaurant&search=tacos&page=2
```

**Response `200 OK`:**
```json
{
  "items": [
    {
      "slug": "buena-vista",
      "business_name": "Buena vista",
      "business_type": "optician"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

---

## Flujo del cliente con explore

```
1. GET /api/v1/explore/categories
   → ["restaurant", "optician"]
   → Frontend renderiza etiquetas/tabs de categoría

2. Usuario selecciona "restaurant"
   GET /api/v1/explore?category=restaurant
   → lista de restaurantes públicos

3. Usuario busca "tacos"
   GET /api/v1/explore?category=restaurant&search=tacos
   → lista filtrada

4. Usuario selecciona una tienda → navega al slug
   GET /s/{slug}          → info del negocio
   GET /s/{slug}/products → catálogo
```

---

## Reglas de visibilidad

Solo aparecen tenants que cumplen **las tres condiciones**:
- `is_public = TRUE` — el negocio activó su tienda pública
- `is_active = TRUE` — la cuenta no está suspendida
- `business_type != 'platform'` — excluye el tenant interno de KARD

Un negocio registrado que no haya activado su tienda (`is_public=False`) es invisible en explore — igual que en storefront.

---

## Archivos creados

```
explore/
├── __init__.py
├── models.py      ← CategoryItem, CategoriesResponse, ExploreStoreItem, ExploreResponse
├── repository.py  ← ExploreRepository (get_categories, list_stores)
├── service.py     ← ExploreService
└── routes.py      ← GET /categories, GET /
```

| Archivo modificado | Cambio |
|---|---|
| `main.py` | Import + `include_router(explore_router, prefix="/api/v1/explore")` |

---

## Verificación en vivo

```bash
# Categorías disponibles
curl http://localhost:8000/api/v1/explore/categories
# → {"categories":[{"business_type":"optician","store_count":1}]}

# Todas las tiendas
curl http://localhost:8000/api/v1/explore
# → {"items":[{"slug":"buena-vista","business_name":"Buena vista","business_type":"optician"}],"total":1,...}
```

> El restaurant no aparece porque tiene `is_public=False`. En cuanto se active desde el dashboard aparecerá automáticamente.

---

## API Frontend actualizada

El doc [`API_Frontend_Cliente.md`](API_Frontend_Cliente.md) tenía estos endpoints marcados como "próximamente" — ahora están implementados y pueden usarse.
