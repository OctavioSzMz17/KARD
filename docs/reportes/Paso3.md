# Reporte de Implementación — PASO 3: Módulo Storefront (Vitrina Pública B2C)
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 3 de 7 — Vitrina Pública  
**Fecha:** 2026-06-18  
**Estado:** ✅ Completado y verificado end-to-end en Neon

---

## Resumen Ejecutivo

Se creó el módulo `storefront/` — la capa B2C pública de KARD. Tres endpoints GET sin autenticación permiten a cualquier visitante explorar el catálogo de un negocio por su slug. Ningún archivo B2B existente fue modificado. Los `consumer_events` se registran automáticamente en cada acceso para alimentar la Fase 3 (motor de IA).

---

## Endpoints Expuestos

**Prefijo:** `/s`  — separado del B2B `/api/v1/` intencionalmente

| Método | Ruta | Descripción | Auth |
|---|---|---|---|
| GET | `/s/{slug}` | Info pública del negocio | No |
| GET | `/s/{slug}/products` | Catálogo paginado | No |
| GET | `/s/{slug}/products/{product_id}` | Detalle de un producto | No |

---

### `GET /s/{slug}`

**Response `200`:**
```json
{
  "slug": "tia-rosa",
  "business_name": "Tia Rosa",
  "business_type": "restaurant"
}
```

Campos intencionalmente omitidos: `email`, `phone`, `config`, `tenant_id`, `is_active`, `created_at`.

---

### `GET /s/{slug}/products`

**Query params:**

| Param | Default | Límites | Descripción |
|---|---|---|---|
| `page` | `1` | ≥ 1 | Página actual |
| `page_size` | `20` | 1–100 | Items por página |
| `search` | `null` | max 200 chars | Búsqueda por nombre |

**Response `200`:**
```json
{
  "items": [
    {
      "product_id": "p_abc123",
      "name": "Tacos de Birria",
      "description": "Tacos de res con consomé",
      "price": "89.00",
      "product_type": "final",
      "metadata": {
        "categoria": "plato_fuerte",
        "tiempo_preparacion_min": 15,
        "picante": false
      },
      "images": [
        {
          "image_id": "img_xyz",
          "url": "https://cdn.example.com/birria.jpg",
          "alt_text": "Tacos de Birria",
          "sort_order": 0
        }
      ]
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 20
}
```

Campos omitidos vs B2B: `tenant_id`, `created_by`, `is_deleted`, `inventory_count`, `low_stock_threshold`.

---

### `GET /s/{slug}/products/{product_id}`

**Response `200`:** mismo objeto que un item de la lista anterior.

**Errores comunes:**

| Código | Causa |
|---|---|
| `404 Storefront not found` | Slug no existe, `is_public=FALSE`, o tenant inactivo |
| `404 Storefront not found` | Producto `insumo` solicitado directamente (se oculta igual que si no existiera) |

---

## Regla de Filtrado: Solo `product_type=final`

Los productos `insumo` (ingredientes, supplies de cocina) son inventario interno y **jamás** aparecen en la vitrina pública. Un visitante que busque "tacos" verá los platillos; nunca verá "Pollo entero" ni "Chile guajillo".

```
Vitrina pública:  product_type = "final"   ← platillos, lentes terminados
Panel B2B:        product_type = "insumo"  ← ingredientes, materiales
                  product_type = "final"   ← los mismos platillos/lentes
```

---

## Seguridad: 404 Indistinguible

Un slug que no existe y un slug cuyo tenant tiene `is_public=FALSE` devuelven **exactamente el mismo 404**. Esto sigue el principio de Rules.md §7.5 — el atacante no puede diferenciar entre "no existe" y "existe pero está privado".

```
GET /s/negocio-que-no-existe   → 404 Storefront not found
GET /s/negocio-privado         → 404 Storefront not found   ← mismo error
GET /s/negocio-inactivo        → 404 Storefront not found   ← mismo error
```

---

## consumer_events: Captura de Comportamiento

Cada acceso a la vitrina genera automáticamente un evento en `consumer_events`:

| Endpoint | event_type | payload |
|---|---|---|
| `GET /s/{slug}` | `view_storefront` | `{"slug": "tia-rosa"}` |
| `GET /s/{slug}/products` | `view_catalog` | `{"slug": "...", "page": 1, "search": null}` |
| `GET /s/{slug}/products/{id}` | `view_product` | `{"slug": "...", "product_id": "p_..."}` |

`consumer_id` es siempre `NULL` por ahora (visitantes anónimos). Se llenará cuando se implemente el módulo `consumers/` (PASO 5).

---

## Arquitectura del Módulo

```
storefront/
├── __init__.py
├── models.py        ← DTOs públicos (sin campos internos)
├── repository.py    ← get_public_tenant_by_slug + record_event
├── service.py       ← Lógica: validar public, filtrar final, emitir eventos
└── routes.py        ← 3 GET sin auth
```

### Reutilización sin duplicación

`StorefrontRepository` delega las queries de productos al `ProductRepository` existente — misma lógica, mismos índices, mismo aislamiento por `tenant_id`. El storefront nunca tiene su propia copia de las queries de productos.

```python
class StorefrontRepository:
    def __init__(self, db):
        self.products = ProductRepository(db)  # reutiliza el existente
```

---

## Archivos Creados / Modificados

### Nuevos (módulo `storefront/`)

| Archivo | Contenido |
|---|---|
| `storefront/__init__.py` | Vacío |
| `storefront/models.py` | `StorefrontTenantResponse`, `StorefrontProductResponse`, `StorefrontProductListResponse`, `StorefrontImageResponse` |
| `storefront/repository.py` | `StorefrontRepository`: resolución de slug + registro de eventos |
| `storefront/service.py` | `StorefrontService`: `get_storefront`, `list_products`, `get_product` |
| `storefront/routes.py` | 3 endpoints GET bajo prefijo `/s` |

### Modificados

| Archivo | Qué cambió |
|---|---|
| `core/exceptions.py` | Nueva excepción `StorefrontNotFoundError` |
| `main.py` | Import de `storefront_router`; handler de `StorefrontNotFoundError`; `app.include_router(storefront_router, prefix="/s")` |

---

## Verificación End-to-End (ejecutado contra Neon)

```
1. Registrar tenant nuevo ("El Fogon Tacos")
   → slug: "el-fogon-tacos" ✅

2. GET /s/el-fogon-tacos con is_public=FALSE
   → StorefrontNotFoundError (404) ✅

3. Activar vitrina (is_public=TRUE)
   → OK ✅

4. GET /s/el-fogon-tacos
   → {"slug":"el-fogon-tacos","business_name":"El Fogon Tacos","business_type":"restaurant"} ✅

5. GET /s/el-fogon-tacos/products
   → {"items":[],"total":0,"page":1,"page_size":20} ✅ (sin productos final aún)

6. consumer_events registrados
   → ["view_storefront", "view_catalog"] ✅
```

---

## Impacto en Módulos Existentes

| Módulo | Impacto |
|---|---|
| `auth/` | Ninguno |
| `products/` | Ninguno — `ProductRepository` reutilizado, no modificado |
| `images/` | Ninguno — `ImageRepository` reutilizado, no modificado |
| `saasadmin/` | Ninguno |
| `main.py` | Añadido 1 import, 1 handler, 1 `include_router` |

---

## Próximo Paso

**PASO 4 — Caché Redis para Catálogo Público**

El endpoint `GET /s/{slug}/products` será el más consultado de toda la plataforma. Sin caché, cada request hace una query a Neon. Con caché Redis:

- **TTL:** 5 minutos
- **Clave:** `storefront:catalog:{slug}:page:{page}:size:{size}`
- **Invalidación:** al crear/editar/eliminar productos en el B2B, se borra la caché del tenant correspondiente

Archivos a modificar: `storefront/service.py`, `products/service.py`, `core/redis_client.py`.
