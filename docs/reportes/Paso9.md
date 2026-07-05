# Reporte de Implementación — PASO 9: Carrito + Pedidos (POS)
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 9 — Punto de Venta del Consumidor  
**Fecha:** 2026-07-05  
**Estado:** ✅ Implementado — pendiente migración 003 en Neon

---

## Qué se construyó

El sistema de punto de venta completo para el consumidor. El carrito vive en Redis (temporal, rápido), y el pedido confirmado se persiste en PostgreSQL (permanente, auditable).

```
Redis                            PostgreSQL
──────────────────────           ──────────────────────────
cart:{consumer_id}:{slug}   →   orders
  { product_id:               order_items
    quantity,
    price_snapshot,
    name }
  TTL: 7 días
```

---

## Acción requerida — correr migración en Neon

Antes de usar los endpoints de órdenes, ejecutar en el **SQL Editor de Neon**:

```sql
-- Copiar contenido de migrations/003_orders_tables.sql
CREATE TABLE orders ( ... );
CREATE TABLE order_items ( ... );
-- índices + trigger
```

El archivo completo está en [`migrations/003_orders_tables.sql`](../../migrations/003_orders_tables.sql).

---

## Módulo Carrito — `cart/`

### Comportamiento

| Escenario | Resultado |
|---|---|
| Usuario agrega producto | Se guarda en Redis con precio snapshot |
| Usuario sale y vuelve | Carrito intacto (TTL 7 días) |
| Usuario va a otra tienda | Carrito de la primera tienda intacto (clave separada) |
| Usuario confirma pedido | Carrito se borra automáticamente |
| Redis no disponible | 503 — el carrito **es** Redis, no hay fallback |

### Endpoints (todos requieren 🔒 Consumer JWT)

#### `POST /api/v1/cart/{slug}/items`
Agrega o actualiza un producto. `quantity=0` elimina el item.

**Request:**
```json
{ "product_id": "p_abc123", "quantity": 2 }
```

**Response `200`:**
```json
{
  "slug": "buena-vista",
  "business_name": "Buena vista",
  "items": [
    {
      "product_id": "p_abc123",
      "name": "Lente Monofocal CR-39",
      "quantity": 2,
      "price_snapshot": "350.00",
      "subtotal": "700.00"
    }
  ],
  "total": "700.00",
  "item_count": 2
}
```

**Validaciones:**
- El producto debe ser `product_type=final` (nunca insumos)
- El producto debe pertenecer al tenant del slug
- El producto no debe estar eliminado (soft-delete)
- Storefront debe ser público y activo

---

#### `GET /api/v1/cart/{slug}`
Ver el carrito actual de una tienda.

**Response:** mismo schema que el POST.

> El precio mostrado es el **price_snapshot** — el precio cuando se agregó al carrito. Puede diferir del precio actual si el negocio lo modificó.

---

#### `DELETE /api/v1/cart/{slug}/items/{product_id}`
Eliminar un producto específico del carrito.

**Response:** carrito actualizado.

---

#### `DELETE /api/v1/cart/{slug}`
Vaciar todo el carrito de una tienda.

**Response:** `204 No Content`

---

## Módulo Pedidos — `orders/`

### Comportamiento en `POST /api/v1/orders`

Secuencia atómica (todo o nada):

```
1. Validar que el storefront es público
2. Leer carrito de Redis
3. Para cada producto:
   a. SELECT FOR UPDATE (row lock — evita overselling)
   b. Verificar stock suficiente
   c. Descontar inventory_count
   d. Registrar InventoryTransaction (auditoría)
4. Crear Order + OrderItems en DB
5. COMMIT
6. Borrar clave Redis del carrito
```

Si cualquier producto no tiene stock suficiente → rollback completo → carrito intacto → `400 InsufficientStockError`.

### Endpoints (todos requieren 🔒 Consumer JWT)

#### `POST /api/v1/orders`
Convierte el carrito en un pedido.

**Request:**
```json
{ "slug": "buena-vista" }
```

**Response `201`:**
```json
{
  "order_id": "ord_3f9a2b1c9d4e",
  "slug": "buena-vista",
  "business_name": "Buena vista",
  "status": "pending",
  "total": "700.00",
  "items": [
    {
      "product_id": "p_abc123",
      "product_name": "Lente Monofocal CR-39",
      "quantity": 2,
      "unit_price": "350.00",
      "subtotal": "700.00"
    }
  ],
  "created_at": "2026-07-05T18:30:00Z"
}
```

**Errores:**
| Status | Error | Causa |
|---|---|---|
| `400` | `"El carrito está vacío"` | No hay items en Redis |
| `400` | `"Stock insuficiente para '...'"` | Menos stock del solicitado |
| `404` | `"Storefront not found"` | Slug inválido o tienda no pública |

---

#### `GET /api/v1/orders`
Lista todos los pedidos del consumer autenticado, orden cronológico inverso.

**Query params:** `?page=1&page_size=20`

**Response:**
```json
{
  "items": [ ...OrderResponse ],
  "total": 5,
  "page": 1,
  "page_size": 20
}
```

---

#### `GET /api/v1/orders/{order_id}`
Detalle de un pedido específico.

Un consumer solo puede ver **sus propios pedidos** — intentar acceder al pedido de otro consumer devuelve `404` (nunca `403`).

---

## Status del pedido

```
pending → paid → fulfilled
    ↓
  canceled
```

| Status | Descripción |
|---|---|
| `pending` | Creado, esperando pago |
| `paid` | Pago confirmado por Stripe (webhook) |
| `fulfilled` | Negocio confirmó entrega |
| `canceled` | Cancelado antes del pago |

> La transición `pending → paid` se implementa en el módulo `billing/` cuando se conecte Stripe.

---

## Price snapshot vs. precio actual

El `price_snapshot` en el carrito es el precio al momento de agregar el producto. El `unit_price` en el pedido es el precio al momento de confirmar (viene de la DB, no del carrito).

**Recomendación para el frontend:** si `price_snapshot != unit_price` al momento del checkout, mostrar aviso al usuario: *"El precio de [producto] cambió de $X a $Y"*.

---

## Archivos creados

```
cart/
├── __init__.py
├── models.py     ← AddToCartRequest, CartItemResponse, CartResponse
├── service.py    ← CartService (Redis ops + validaciones)
└── routes.py     ← 4 endpoints

orders/
├── __init__.py
├── models.py     ← CreateOrderRequest, OrderResponse, OrderListResponse
├── repository.py ← OrderRepository
├── service.py    ← OrderService (transacción atómica)
└── routes.py     ← 3 endpoints

migrations/
└── 003_orders_tables.sql ← ¡ejecutar en Neon!
```

| Archivo modificado | Cambio |
|---|---|
| `db/models.py` | `+ Order`, `+ OrderItem` |
| `core/exceptions.py` | `+ CartEmptyError`, `+ CartUnavailableError`, `+ OrderNotFoundError` |
| `main.py` | Imports + handlers 400/503/404 + `include_router` x2 |

---

## Flujo completo Postman

```
1. Login consumer
   POST /api/v1/consumers/login
   → guardar access_token

2. Agregar al carrito
   POST /api/v1/cart/buena-vista/items
   Authorization: Bearer {token}
   { "product_id": "p_xxx", "quantity": 2 }

3. Ver carrito
   GET /api/v1/cart/buena-vista
   Authorization: Bearer {token}

4. Confirmar pedido
   POST /api/v1/orders
   Authorization: Bearer {token}
   { "slug": "buena-vista" }
   → order_id, status: "pending"

5. Ver mis pedidos
   GET /api/v1/orders
   Authorization: Bearer {token}
```
