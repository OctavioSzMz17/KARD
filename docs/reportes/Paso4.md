# Reporte de Implementación — PASO 4: Caché Redis para Catálogo Público
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 4 de 7 — Redis Cache  
**Fecha:** 2026-06-18  
**Estado:** ✅ Completado y verificado (incluyendo degradación graceful)

---

## Resumen Ejecutivo

El endpoint `GET /s/{slug}/products` — el más consultado de toda la plataforma — ahora sirve resultados desde Redis con TTL de 5 minutos. Cuando el B2B admin modifica productos, la caché del tenant se invalida automáticamente. Si Redis no está disponible, el sistema continúa sirviendo desde la DB sin errores.

---

## Estrategia de Caché

### Clave de caché

```
storefront:catalog:{slug}:page:{page}:size:{page_size}:search:{search}
```

**Ejemplos:**
```
storefront:catalog:tia-rosa:page:1:size:20:search:
storefront:catalog:tia-rosa:page:2:size:20:search:
storefront:catalog:tia-rosa:page:1:size:20:search:tacos
```

Cada combinación de slug + paginación + búsqueda tiene su propia entrada. La invalidación borra todas con el patrón `storefront:catalog:{slug}:*`.

### TTL

**300 segundos (5 minutos).** Un producto editado en el B2B puede verse en la vitrina con hasta 5 min de delay... salvo que la invalidación activa lo limpie antes (lo que ocurre en el caso normal).

### Invalidación activa

Se activa en tres operaciones B2B que cambian el catálogo visible:

| Operación B2B | Método | Invalida caché |
|---|---|---|
| Crear producto | `ProductService.create_product` | Sí |
| Editar producto | `ProductService.update_product` | Sí |
| Borrar producto (soft) | `ProductService.soft_delete_product` | Sí |

La invalidación solo ocurre si el tenant tiene `slug` asignado (los tenants sin vitrina no tienen qué invalidar).

---

## Flujo de `GET /s/{slug}/products` con caché

```
Request → StorefrontService.list_products(slug, page, size, search)
    │
    ├─ 1. Validar tenant público (siempre DB)
    │
    ├─ 2. Construir cache_key
    │
    ├─ 3. Redis disponible?
    │       ├─ Sí → GET cache_key
    │       │         ├─ HIT  → deserializar JSON → return (+ event)
    │       │         └─ MISS → continuar a paso 4
    │       └─ No  → continuar a paso 4
    │
    ├─ 4. Query a DB (ProductRepository.list_products)
    │
    ├─ 5. Construir StorefrontProductListResponse
    │
    ├─ 6. Redis disponible?
    │       └─ Sí → SET cache_key (TTL=300s)
    │
    ├─ 7. Registrar consumer_event (view_catalog, cache: "hit"|"miss")
    │
    └─ 8. Commit + return
```

---

## Flujo de invalidación (mutación B2B)

```
ProductService.create_product / update_product / soft_delete_product
    │
    └─ (después del commit exitoso)
            │
            └─ _bust_storefront_cache(tenant_id)
                    │
                    ├─ Query: SELECT slug FROM tenants WHERE id=tenant_id
                    │
                    ├─ slug = NULL? → return (sin caché que invalidar)
                    │
                    └─ invalidate_storefront_cache(slug)
                            │
                            ├─ Redis disponible?
                            │       └─ No → return silencioso
                            │
                            └─ SCAN storefront:catalog:{slug}:* → DEL todas
```

---

## Degradación Graceful

Redis es una optimización, no un requisito de correctitud. En todos los casos de fallo:

| Escenario | Comportamiento |
|---|---|
| Redis no disponible al arrancar | Se detecta en el primer `ping()`, `_unavailable=True`, jamás se reintenta |
| Redis cae en producción | `get_redis()` retorna `None`, se salta silenciosamente |
| `GET` de caché falla | Warning en log, se cae a DB query |
| `SET` de caché falla | Warning en log, respuesta se sirve igual sin cachear |
| `SCAN/DEL` de invalidación falla | Warning en log, TTL natural expira en ≤ 5 min |

**Verificado:** con Redis URL inválida (`redis://localhost:9999`), `list_products` responde correctamente desde DB sin ninguna excepción.

---

## consumer_events: campo `cache` añadido al payload

El evento `view_catalog` ahora incluye si el resultado vino de caché o no:

```json
{
  "event_type": "view_catalog",
  "payload": {
    "slug": "tia-rosa",
    "page": 1,
    "search": null,
    "cache": "hit"    ← "hit" | "miss"
  }
}
```

Esto permite a la Fase 3 (motor de IA) medir el cache hit rate y optimizar TTLs por tenant.

---

## Archivos Modificados

### `core/redis_client.py` ← dos adiciones

```python
STOREFRONT_CACHE_TTL = 300  # constante exportada para referencia en otros módulos

def invalidate_storefront_cache(slug: str) -> None:
    """SCAN storefront:catalog:{slug}:* → DEL. Falla silenciosamente si Redis está caído."""
```

### `storefront/service.py` ← lógica de caché en `list_products`

- Lee de Redis antes de consultar la DB
- Escribe a Redis después de construir la respuesta
- Ambos paths registran el `consumer_event` con `cache: "hit"/"miss"`

### `products/service.py` ← invalidación en 3 métodos + helper

```python
def _bust_storefront_cache(self, tenant_id: str) -> None:
    tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant and tenant.slug:
        invalidate_storefront_cache(tenant.slug)
```

Llamado al final de `create_product`, `update_product`, `soft_delete_product`.

---

## Verificación End-to-End (Docker + Neon — 2026-06-18)

Test ejecutado via HTTP contra el contenedor `kard-backend-1` con Redis real (`kard-redis-1`).

### Secuencia de requests

| # | Petición | Cache | Tiempo | Resultado |
|---|---|---|---|---|
| 1 | `GET /s/cache-test-resto/products` | MISS → DB | 1306 ms | `total=1` |
| 2 | `GET /s/cache-test-resto/products` | HIT → Redis | 615 ms | `total=1` |
| — | `PATCH /api/v1/products/{id}` (editar) | — invalida caché — | — | nombre actualizado |
| 3 | `GET /s/cache-test-resto/products` | MISS → DB | 1201 ms | nombre nuevo visible |
| 4 | `GET /s/cache-test-resto/products` | HIT → Redis | 1067 ms | nombre nuevo cacheado |

**Speedup MISS→HIT:** 2.1x  
> El speedup conservador se debe a que la validación del tenant (`get_public_tenant_by_slug`) y el `record_event + commit` siguen yendo a Neon cloud en todos los requests. Con DB local, el ahorro en la query de productos sería mucho mayor.

### consumer_events registrados (campo `cache` en payload)

```
view_catalog  cache=miss   ← 1ra petición, query a DB
view_catalog  cache=hit    ← 2da petición, servida desde Redis
view_catalog  cache=miss   ← 3ra petición, cache invalidada por edición
view_catalog  cache=hit    ← 4ta petición, Redis con datos frescos
```

La invalidación activa funciona correctamente: el nombre editado aparece inmediatamente en la 3ra petición sin esperar el TTL de 5 minutos.

### Degradación graceful verificada

Con `REDIS_URL=redis://localhost:9999` (Redis inaccesible):
- `get_redis()` → `None` sin excepción
- `invalidate_storefront_cache()` → silencioso
- `list_products()` → responde desde DB sin ningún error

---

## Impacto en Módulos Existentes

| Módulo | Impacto |
|---|---|
| `storefront/service.py` | Caché añadida a `list_products` |
| `products/service.py` | `_bust_storefront_cache()` + llamadas en 3 métodos |
| `core/redis_client.py` | `STOREFRONT_CACHE_TTL` + `invalidate_storefront_cache()` |
| Auth, Images, SAASAdmin | Sin cambios |

---

## Próximo Paso

**PASO 5 — Módulo `consumers/` (Identidad Global del Consumidor)**

Nuevos endpoints públicos:
```
POST /consumers/register   → Crear cuenta global (sin tenant_id)
POST /consumers/login      → JWT de consumidor
GET  /consumers/me         → Perfil propio
```

El JWT de consumidor es distinto al B2B:
```json
{ "consumer_id": "...", "type": "consumer" }
```

Sin `tenant_id` — el tenant siempre se resuelve del slug en la URL.  
Al autenticarse, el `consumer_id` comenzará a aparecer en `consumer_events` en lugar de `NULL`.
