# KARD — API Reference: Images

Base URL: `http://localhost:8000`
Prefijo: `/api/v1/products/{product_id}/images`

Las imágenes son **URL-based**: KARD almacena la URL que tú le pases — puede ser Google Drive, S3, Cloudinary, ruta local, o cualquier gestor de archivos. El backend nunca maneja el archivo binario.

Todos los endpoints requieren:
```
Authorization: Bearer <access_token>
```

El `tenant_id` siempre viene del token. Un tenant nunca puede ver ni modificar las imágenes de otro tenant — cualquier intento devuelve `404`, no `403` (no revelamos si el recurso existe).

---

## Índice

1. [Añadir imagen](#1-añadir-imagen--post)
2. [Listar imágenes](#2-listar-imágenes--get)
3. [Actualizar imagen](#3-actualizar-imagen--patch)
4. [Eliminar imagen](#4-eliminar-imagen--delete)
5. [Images en ProductResponse](#5-images-en-productresponse)
6. [Errores](#6-errores)
7. [Referencia de campos](#7-referencia-de-campos)

---

## 1. Añadir imagen — `POST`

```
POST /api/v1/products/{product_id}/images
```

Añade una imagen al producto. El `sort_order` controla el orden de visualización — `0` es la imagen principal.

**Request:**
```json
{
  "url": "https://drive.google.com/uc?id=abc123",
  "alt_text": "Tacos al Pastor — vista frontal",
  "sort_order": 0
}
```

```json
{
  "url": "https://drive.google.com/uc?id=def456",
  "alt_text": "Tacos al Pastor — detalle de la carne",
  "sort_order": 1
}
```

```json
{
  "url": "/uploads/productos/lente-cr39.jpg",
  "sort_order": 0
}
```

> `alt_text` y `sort_order` son opcionales. Si omites `sort_order` el default es `0`.

**Respuesta `201`:**
```json
{
  "image_id": "img_568a7e39b626",
  "owner_type": "product",
  "owner_id": "p_570e625f0ea5",
  "url": "https://drive.google.com/uc?id=abc123",
  "alt_text": "Tacos al Pastor — vista frontal",
  "sort_order": 0,
  "created_at": "2026-06-09T00:34:11.693514Z"
}
```

---

## 2. Listar imágenes — `GET`

```
GET /api/v1/products/{product_id}/images
```

Sin body, sin params. Devuelve todas las imágenes del producto ordenadas por `sort_order` ASC, con `created_at` ASC como desempate.

**Respuesta `200`:**
```json
[
  {
    "image_id": "img_568a7e39b626",
    "owner_type": "product",
    "owner_id": "p_570e625f0ea5",
    "url": "https://drive.google.com/uc?id=abc123",
    "alt_text": "Tacos al Pastor — vista frontal",
    "sort_order": 0,
    "created_at": "2026-06-09T00:34:11.693514Z"
  },
  {
    "image_id": "img_87dabeccaf01",
    "owner_type": "product",
    "owner_id": "p_570e625f0ea5",
    "url": "https://drive.google.com/uc?id=def456",
    "alt_text": "Tacos al Pastor — detalle de la carne",
    "sort_order": 1,
    "created_at": "2026-06-09T00:34:13.384000Z"
  }
]
```

Si el producto no tiene imágenes devuelve `[]`.

---

## 3. Actualizar imagen — `PATCH`

```
PATCH /api/v1/products/{product_id}/images/{image_id}
```

Actualización parcial — solo manda los campos que quieras cambiar.

**Solo cambiar URL:**
```json
{
  "url": "https://drive.google.com/uc?id=NUEVA_URL"
}
```

**Solo cambiar orden (promover a imagen principal):**
```json
{
  "sort_order": 0
}
```

**Cambiar alt_text:**
```json
{
  "alt_text": "Tacos al Pastor con salsa verde y cilantro"
}
```

**Cambiar todo a la vez:**
```json
{
  "url": "https://drive.google.com/uc?id=NUEVA_URL",
  "alt_text": "Nueva descripción",
  "sort_order": 2
}
```

**Respuesta `200`:**
```json
{
  "image_id": "img_568a7e39b626",
  "owner_type": "product",
  "owner_id": "p_570e625f0ea5",
  "url": "https://drive.google.com/uc?id=NUEVA_URL",
  "alt_text": "Tacos al Pastor con salsa verde y cilantro",
  "sort_order": 2,
  "created_at": "2026-06-09T00:34:11.693514Z"
}
```

---

## 4. Eliminar imagen — `DELETE`

```
DELETE /api/v1/products/{product_id}/images/{image_id}
```

Sin body. **Hard delete** — la fila se borra permanentemente de la base de datos (las imágenes son assets, no registros de negocio, no requieren soft-delete). El audit log queda registrado.

**Respuesta `204`:** sin body.

> Eliminar una imagen **no afecta** a otras imágenes del mismo producto ni a otros productos que usen la misma URL — cada fila es independiente (patrón Option A del polimorfismo).

---

## 5. Images en ProductResponse

Todos los endpoints de productos (`GET`, `POST`, `PATCH` sobre productos) devuelven las imágenes embebidas en la respuesta — no necesitas hacer una llamada extra:

```
GET /api/v1/products/{product_id}
```

```json
{
  "product_id": "p_570e625f0ea5",
  "tenant_id": "t_9a40982067c4",
  "product_type": "final",
  "name": "Tacos al Pastor",
  "description": null,
  "price": "4.50",
  "inventory_count": 50,
  "low_stock_threshold": 10,
  "metadata": {
    "categoria": "plato_fuerte",
    "tiempo_preparacion_min": 8,
    "picante": true
  },
  "is_deleted": false,
  "images": [
    {
      "image_id": "img_568a7e39b626",
      "owner_type": "product",
      "owner_id": "p_570e625f0ea5",
      "url": "https://drive.google.com/uc?id=abc123",
      "alt_text": "Tacos al Pastor — vista frontal",
      "sort_order": 0,
      "created_at": "2026-06-09T00:34:11.693514Z"
    },
    {
      "image_id": "img_87dabeccaf01",
      "owner_type": "product",
      "owner_id": "p_570e625f0ea5",
      "url": "https://drive.google.com/uc?id=def456",
      "alt_text": "Tacos al Pastor — detalle de la carne",
      "sort_order": 1,
      "created_at": "2026-06-09T00:34:13.384000Z"
    }
  ]
}
```

Si el producto no tiene imágenes el campo llega como `"images": []`.

---

## 6. Errores

Todos los errores siguen el envelope estándar:
```json
{
  "success": false,
  "error": "Mensaje del error"
}
```

| Código | Error | Cuándo ocurre |
|---|---|---|
| `401` | `"Not authenticated"` | Sin token o token inválido |
| `404` | `"Product not found"` | El `product_id` no existe, fue soft-deleted, o pertenece a otro tenant |
| `404` | `"Image not found"` | El `image_id` no existe, pertenece a otro producto, o a otro tenant |
| `422` | Errores de validación Pydantic | `url` vacía, `sort_order` negativo, tipos incorrectos |

**Casos de error para probar en Postman:**

| Qué probar | Cómo | Respuesta esperada |
|---|---|---|
| `product_id` que no existe | `POST /products/p_inventado/images` con body válido | `404` "Product not found" |
| `image_id` que no existe | `PATCH /products/{id}/images/img_inventado` | `404` "Image not found" |
| `image_id` de otro producto (mismo tenant) | Usar el `image_id` de un producto A en la ruta del producto B | `404` "Image not found" |
| Token de otro tenant | Usar token de óptica para acceder a imágenes de restaurante | `404` "Product not found" |
| `url` vacía | `{"url": ""}` | `422` validation error |
| `sort_order` negativo | `{"url": "...", "sort_order": -1}` | `422` validation error |
| Sin token | Cualquier ruta sin `Authorization` | `401` "Not authenticated" |

---

## 7. Referencia de campos

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `image_id` | string | — (respuesta) | Identificador único: `img_xxxxxxxxxxxx` |
| `owner_type` | string | — (respuesta) | Siempre `"product"` en esta versión |
| `owner_id` | string | — (respuesta) | `product_id` al que pertenece la imagen |
| `url` | string (1–2000 chars) | ✅ en create | URL de la imagen — Google Drive, S3, local, etc. |
| `alt_text` | string (máx 255) | ⬜ opcional | Texto alternativo / descripción accesible |
| `sort_order` | integer ≥ 0 | ⬜ opcional (default `0`) | Posición en la galería — `0` = imagen principal |
| `created_at` | datetime ISO 8601 | — (respuesta) | Fecha y hora de creación en UTC |

### Convención de `sort_order`

- `sort_order: 0` → imagen principal (portada del producto)
- `sort_order: 1, 2, 3...` → galería adicional
- Dos imágenes con el mismo `sort_order` se ordenan por `created_at` ASC como desempate
- No hay reordenamiento automático — si borras `sort_order: 0`, la siguiente imagen no sube sola; actualízala con `PATCH`
