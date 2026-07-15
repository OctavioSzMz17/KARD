# KARD — API Reference: Vista del Cliente (B2C)
**Versión:** MVP Fase 1  
**Base URL:** `http://localhost:8000` (dev) · configurable en `APP_URL` del `.env`  
**Formato:** JSON en todos los requests y responses  
**Encoding:** UTF-8

---

## Índice

1. [Convenciones generales](#1-convenciones-generales)
2. [Autenticación del consumidor](#2-autenticación-del-consumidor)
3. [Storefront — Tienda pública](#3-storefront--tienda-pública)
4. [Descubrimiento — Explorar tiendas](#4-descubrimiento--explorar-tiendas) *(próximamente)*
5. [Carrito y Pedidos](#5-carrito-y-pedidos) *(próximamente)*
6. [Errores](#6-errores)
7. [Metadatos por tipo de negocio](#7-metadatos-por-tipo-de-negocio)
8. [Flujo completo de compra](#8-flujo-completo-de-compra)

---

## 1. Convenciones generales

### Header de autorización
```
Authorization: Bearer <token>
```
Solo se requiere en los endpoints marcados con 🔒. Los demás son públicos.

### Respuesta exitosa
No hay sobre. El objeto/array llega directo:
```json
{ "product_id": "p_abc123", "name": "...", ... }
```

### Paginación
Los listados siempre tienen esta estructura:
```json
{
  "items": [...],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```
Parámetros de query: `?page=1&page_size=20`  
Límite máximo: `page_size=100`

### Fechas
ISO 8601 con timezone: `"2026-06-18T16:35:34.864Z"`

---

## 2. Autenticación del consumidor

### Dos tipos de identidad en la plataforma

La plataforma tiene **dos tipos de usuario completamente separados**. El frontend debe manejar ambos de forma independiente — sus tokens no son intercambiables y sus endpoints son distintos.

#### Consumer (cliente B2C) — corresponde a esta vista
Login via `POST /api/v1/consumers/login`. Respuesta:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "consumer_id": "con_3f9a2b1c9d4e"
}
```
El JWT interno carga: `{ "consumer_id": "...", "type": "consumer" }`.  
Sin `tenant_id` ni `role` — el contexto del negocio siempre viene del slug en la URL.

#### User B2B (dueño/empleado del negocio) — NO corresponde a esta vista
Login via `POST /api/v1/auth/login`. Respuesta:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user_id": "u_abc123",
  "tenant_id": "t_xyz456",
  "role": "business_admin",
  "business_type": "restaurant"
}
```
El JWT interno carga: `{ "user_id": "...", "tenant_id": "...", "role": "...", "business_type": "..." }`.  
No tiene `type: "consumer"`. El backend rechaza estos tokens en rutas de consumer y viceversa.

**Cómo distinguirlos en el frontend:**
```typescript
// Si la respuesta tiene consumer_id → es un consumer (cliente)
// Si la respuesta tiene role + tenant_id → es un user B2B (negocio)
const isConsumer = (response) => 'consumer_id' in response;
```

---

Un consumer tiene una identidad global — un solo registro da acceso a todas las tiendas de la plataforma. No hay `tenant_id` en el token.

### `POST /api/v1/consumers/register`
Crea una cuenta nueva. Devuelve un JWT listo para usar.

**Request**
```json
{
  "email": "maria@ejemplo.com",
  "password": "MiPassword123",
  "name": "María García"
}
```

| Campo | Tipo | Requerido | Reglas |
|---|---|---|---|
| `email` | string | ✅ | email válido, único en la plataforma |
| `password` | string | ✅ | mínimo 8 chars, al menos 1 letra y 1 dígito |
| `name` | string | ❌ | máximo 100 chars |

**Response `201 Created`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "consumer_id": "con_3f9a2b1c9d4e"
}
```

**Errores**
| Status | Error | Causa |
|---|---|---|
| `400` | `"Email is already registered"` | El email ya tiene cuenta |
| `422` | Validation error | Password sin dígitos, email malformado, etc. |

---

### `POST /api/v1/consumers/login`
Autentica con email y contraseña.

**Request**
```json
{
  "email": "maria@ejemplo.com",
  "password": "MiPassword123"
}
```

**Response `200 OK`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "consumer_id": "con_3f9a2b1c9d4e"
}
```

**Errores**
| Status | Error | Causa |
|---|---|---|
| `401` | `"Invalid email or password"` | Credenciales incorrectas (intencionalmente ambiguo) |

---

### `GET /api/v1/consumers/me` 🔒
Devuelve el perfil del consumer autenticado.

**Response `200 OK`**
```json
{
  "consumer_id": "con_3f9a2b1c9d4e",
  "email": "maria@ejemplo.com",
  "name": "María García",
  "is_active": true,
  "created_at": "2026-06-18T16:35:34.864Z"
}
```

---

## 3. Storefront — Tienda pública

Los tres endpoints de storefront son **completamente públicos** — no requieren token. El contexto del negocio viene siempre del `{slug}` en la URL.

> Un slug que no existe o pertenece a una tienda no pública devuelve `404` — intencionalmente indistinguible.

---

### `GET /s/{slug}`
Información pública del negocio.

**Response `200 OK`**
```json
{
  "slug": "optica-vision-total",
  "business_name": "Óptica Visión Total",
  "business_type": "optician"
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `slug` | string | Identificador URL del negocio |
| `business_name` | string | Nombre comercial para mostrar en la UI |
| `business_type` | `"restaurant"` \| `"optician"` | Determina qué campos de `metadata` existen en los productos |

**Errores**
| Status | Error | Causa |
|---|---|---|
| `404` | `"Storefront not found"` | Slug inválido o tienda no pública |

---

### `GET /s/{slug}/products`
Catálogo paginado — **solo productos finales** (nunca insumos internos).

**Query params**

| Parámetro | Tipo | Default | Descripción |
|---|---|---|---|
| `page` | int | `1` | Número de página (≥1) |
| `page_size` | int | `20` | Items por página (1–100) |
| `search` | string | — | Búsqueda por nombre (case-insensitive, parcial) |

**Response `200 OK`**
```json
{
  "items": [
    {
      "product_id": "p_abc123def456",
      "name": "Lente Monofocal CR-39",
      "description": "Lente orgánico estándar para miopía o hipermetropía leve.",
      "price": "350.00",
      "product_type": "final",
      "metadata": {
        "esfera": "-2.50",
        "cilindro": "0.00",
        "eje": 0,
        "material": "CR-39"
      },
      "images": [
        {
          "image_id": "img_xyz",
          "url": "https://...",
          "alt_text": "Lente CR-39",
          "sort_order": 0
        }
      ]
    }
  ],
  "total": 5,
  "page": 1,
  "page_size": 20
}
```

> **Caché:** Este endpoint tiene caché en Redis (TTL 5 min). El caché se invalida automáticamente cuando el negocio modifica su catálogo.

---

### `GET /s/{slug}/products/{product_id}`
Detalle de un producto.

**Response `200 OK`** — mismo schema que un item de la lista:
```json
{
  "product_id": "p_abc123def456",
  "name": "Lente Monofocal CR-39",
  "description": "Lente orgánico estándar para miopía o hipermetropía leve.",
  "price": "350.00",
  "product_type": "final",
  "metadata": { ... },
  "images": [ ... ]
}
```

**Errores**
| Status | Error | Causa |
|---|---|---|
| `404` | `"Product not found"` | Producto inválido, eliminado, o de otra tienda |

---

## 4. Descubrimiento — Explorar tiendas

Permite al cliente navegar el marketplace sin conocer ningún slug.

---

### `GET /api/v1/explore/categories`
Devuelve los tipos de negocio disponibles con su conteo de tiendas. Úsalo para renderizar las etiquetas/tabs de categoría.

**Response `200 OK`:**
```json
{
  "categories": [
    { "business_type": "restaurant", "store_count": 3 },
    { "business_type": "optician",   "store_count": 1 }
  ]
}
```

---

### `GET /api/v1/explore`
Lista paginada de tiendas públicas activas.

**Query params:**

| Parámetro | Tipo | Default | Descripción |
|---|---|---|---|
| `category` | string | — | Filtra por tipo de negocio (`restaurant`, `optician`) |
| `search` | string | — | Búsqueda parcial en nombre del negocio |
| `page` | int | `1` | Página (≥1) |
| `page_size` | int | `20` | Items por página (1–100) |

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

> Solo aparecen tiendas con `is_public=True`. Un negocio que no haya activado su tienda pública no aparece aquí ni en `/s/{slug}`.

---

## 5. Carrito y Pedidos

El carrito es **por tienda** — no se mezclan productos de distintos negocios. Vive en el servidor (Redis) asociado al consumer, con duración de 7 días. Todos los endpoints requieren 🔒 **Consumer JWT**.

### 5.1 Carrito

#### `POST /api/v1/cart/{slug}/items`
Agrega o actualiza un producto. La cantidad es **absoluta** (no incremental). `quantity: 0` elimina el item.

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
      "price_snapshot": "50.00",
      "subtotal": "100.00"
    }
  ],
  "total": "100.00",
  "item_count": 2
}
```
> `price_snapshot` = precio al momento de agregar. Puede diferir del precio actual si el negocio lo cambió.

#### `GET /api/v1/cart/{slug}`
Ver el carrito actual de una tienda. Mismo schema que arriba.

#### `DELETE /api/v1/cart/{slug}/items/{product_id}`
Quita un producto. Devuelve el carrito actualizado.

#### `DELETE /api/v1/cart/{slug}`
Vacía todo el carrito. Responde `204 No Content`.

---

### 5.2 Pedidos

#### `POST /api/v1/orders` — Confirmar pedido (checkout)

Convierte el carrito en pedido. Descuenta el stock del producto **y de sus ingredientes** (si es un platillo con receta), todo atómico.

**Request:**
```json
{
  "slug": "buena-vista",
  "on_insufficient_stock": "reject"
}
```

| Campo | Valores | Default | Qué hace |
|---|---|---|---|
| `slug` | string | — | Tienda del carrito |
| `on_insufficient_stock` | `"reject"` \| `"skip"` | `"reject"` | Qué hacer si falta stock |

**Response `201` (éxito):**
```json
{
  "order_id": "ord_3f9a2b1c9d4e",
  "slug": "buena-vista",
  "business_name": "Buena vista",
  "status": "pending",
  "total": "100.00",
  "items": [
    { "product_id": "p_abc123", "product_name": "Lente Monofocal CR-39",
      "quantity": 2, "unit_price": "50.00", "subtotal": "100.00" }
  ],
  "skipped": [],
  "created_at": "2026-07-11T18:30:00Z"
}
```

---

### 5.3 ⭐ Flujo de confirmación cuando falta stock

Este es el flujo clave que el front debe manejar:

```
1. Front llama POST /orders con on_insufficient_stock:"reject" (o sin el campo)
        │
        ├─ Todo OK → 201, pedido creado
        │
        └─ Falta stock → 409 con code:"INSUFFICIENT_STOCK" y el detalle
                │
                ▼
        Front muestra al usuario qué falta y pregunta:
        ┌─────────────────────────────────────────────┐
        │ "No hay stock de X. ¿Qué quieres hacer?"    │
        │  [ Continuar sin esos ]   [ Cancelar ]      │
        └─────────────────────────────────────────────┘
                │                        │
    "Continuar" │                        │ "Cancelar"
                ▼                        ▼
    Reenvía POST /orders          No hace nada.
    con on_insufficient_stock:    El carrito queda intacto.
    "skip"  → 201 con `skipped`
```

**Respuesta `409` (conflicto de stock):**
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

**Cómo interpretar `unavailable[].reason`:**

| `reason` | Significado | Campos extra |
|---|---|---|
| `product_shortage` | El producto no tiene suficiente stock propio | `available` (stock actual) |
| `ingredient_shortage` | Un platillo no tiene suficientes ingredientes | `missing_ingredients[]` |
| `product_and_ingredient` | Faltan ambos | `available` + `missing_ingredients[]` |
| `unavailable` | El producto fue eliminado o ya no existe | — |

- `details.all_unavailable: true` → **ningún** producto se puede vender; ni con `skip` se crearía pedido (el `skip` en este caso devuelve otro 409).

**Segundo intento con `skip`** → `201`, y los omitidos llegan en `skipped`:
```json
{
  "order_id": "ord_...",
  "status": "pending",
  "items": [ /* lo que sí se pudo */ ],
  "skipped": [
    { "product_id": "p_tacos", "product_name": "Tacos al pastor", "reason": "ingredient_shortage" }
  ],
  "total": "...",
  "...": "..."
}
```
> Con `skip`, si el pedido se crea, **el carrito se vacía completo**. Muestra al usuario los `skipped` para que sepa qué no se incluyó.

---

#### `GET /api/v1/orders` — Mis pedidos
Lista paginada de los pedidos del consumer (todas las tiendas). Params `?page=1&page_size=20`.

```json
{ "items": [ /* OrderResponse */ ], "total": 5, "page": 1, "page_size": 20 }
```

#### `GET /api/v1/orders/{order_id}` — Detalle
Un consumer solo ve **sus** pedidos; otro id devuelve `404`.

---

### 5.4 Estados del pedido

```
pending → paid → fulfilled
   └──────────→ canceled
```
`pending` (creado, sin pagar) · `paid` (pago Stripe confirmado) · `fulfilled` (entregado) · `canceled`.

> El pago (`pending → paid`) se conectará cuando Stripe esté activo. Por ahora el pedido nace en `pending`.

---

## 6. Errores

Todos los errores siguen el mismo sobre:
```json
{
  "success": false,
  "error": "Mensaje legible para el usuario"
}
```

### Códigos usados en la vista del cliente

| HTTP | Cuándo ocurre |
|---|---|
| `400` | Email ya registrado, carrito vacío, validación de negocio |
| `401` | Token inválido / expirado / no enviado |
| `404` | Slug no existe, producto o pedido no encontrado |
| `409` | **Conflicto de stock en checkout** — trae `code: "INSUFFICIENT_STOCK"` y `details.unavailable[]` (ver §5.3) |
| `422` | Validación de Pydantic: campo faltante, tipo incorrecto, password débil |
| `503` | Carrito no disponible (Redis caído) — reintentar |
| `500` | Error interno — reportar al backend |

> **Tip para el front:** revisa siempre el campo `code` cuando exista. Hoy el único código estructurado es `INSUFFICIENT_STOCK` (409), pensado para que puedas ramificar la lógica sin parsear el mensaje de texto.

### Ejemplo de error 422 (validation)
```json
{
  "detail": [
    {
      "loc": ["body", "password"],
      "msg": "Password must contain at least one digit",
      "type": "value_error"
    }
  ]
}
```
> Los errores 422 de Pydantic NO usan el sobre `{"success": false}`. Llegan directo como `{"detail": [...]}`.

---

## 7. Metadatos por tipo de negocio

El campo `metadata` en un producto cambia de forma según `business_type`.  
El frontend debe renderizar los campos correctos según el tipo de tienda.

### `business_type: "restaurant"`

```typescript
type RestaurantProductMeta = {
  categoria: "entrada" | "plato_fuerte" | "postre" | "bebida" | "guarnicion";
  tiempo_preparacion_min: number;  // minutos, ej: 15
  picante: boolean;
  alergenos?: string[];            // ej: ["gluten", "lacteos"]
}
```

**Ejemplo:**
```json
{
  "categoria": "plato_fuerte",
  "tiempo_preparacion_min": 20,
  "picante": true,
  "alergenos": ["gluten"]
}
```

### `business_type: "optician"`

```typescript
type OpticianProductMeta = {
  esfera: string;    // ej: "-2.50", "+1.25", "0.00"
  cilindro: string;  // ej: "-1.00", "0.00"
  eje: number;       // 0–180 grados
  material: string;  // ej: "CR-39", "Policarbonato", "Trivex", "Alto Índice 1.67"
}
```

**Ejemplo:**
```json
{
  "esfera": "-2.50",
  "cilindro": "-0.50",
  "eje": 90,
  "material": "Policarbonato"
}
```

---

## 8. Flujo completo de compra

```
┌─────────────────────────────────────────────────────────────────┐
│                    FLUJO CONSUMER (MVP)                         │
└─────────────────────────────────────────────────────────────────┘

1. DESCUBRIMIENTO (próximamente)
   GET /api/v1/explore?category=restaurant
   → lista de tiendas públicas

2. TIENDA
   GET /s/{slug}
   → nombre, tipo de negocio

3. CATÁLOGO
   GET /s/{slug}/products?page=1&search=...
   → lista paginada de productos

4. DETALLE
   GET /s/{slug}/products/{product_id}
   → precio, descripción, imágenes, metadata

5. AUTH (si no tiene sesión)
   POST /api/v1/consumers/register  → cuenta nueva + JWT
   POST /api/v1/consumers/login     → JWT existente

6. PEDIDO (próximamente)
   POST /api/v1/orders
     body: { slug, items: [{ product_id, quantity }] }
   → order_id

7. PAGO (próximamente — requiere Billing/Stripe activo)
   POST /api/v1/orders/{order_id}/pay
   → redirect_url a Stripe Checkout
```

---

## Tiendas demo disponibles

| Slug | Tipo | Estado |
|---|---|---|
| `optica-vision-total` | optician | público, 5 productos |
| *(slug del restaurant)* | restaurant | según tu seed |

Para conocer el slug del restaurant existente:
```
GET /api/v1/auth/login  →  token
GET /api/v1/products    →  products con tenant_id
```
O desde Swagger: `http://localhost:8000/docs`
