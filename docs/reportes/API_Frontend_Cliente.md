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

> 🚧 **Próximamente — PASO 8/9**

El carrito es **por tienda** — no se mezclan productos de distintos negocios.

**Endpoints planeados:**

```
POST   /api/v1/orders              → crear pedido (consumer JWT requerido)
GET    /api/v1/orders/{order_id}   → detalle del pedido
POST   /api/v1/orders/{order_id}/pay → iniciar pago con Stripe
```

**Body de crear pedido (borrador):**
```json
{
  "slug": "optica-vision-total",
  "items": [
    { "product_id": "p_abc123", "quantity": 1 }
  ]
}
```

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
| `400` | Email ya registrado, validación de negocio |
| `401` | Token inválido / expirado / no enviado |
| `404` | Slug no existe, producto no encontrado |
| `422` | Validación de Pydantic: campo faltante, tipo incorrecto, password débil |
| `500` | Error interno — reportar al backend |

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
