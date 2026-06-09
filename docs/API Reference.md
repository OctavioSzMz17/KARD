# KARD — API Reference

Base URL: `http://localhost:8000`
Versión: `v1`
Prefijo global: `/api/v1`

Todos los endpoints protegidos requieren el header:
```
Authorization: Bearer <access_token>
```

El `access_token` se obtiene en `POST /api/v1/auth/login` y contiene embebidos `user_id`, `tenant_id`, `business_type` y `role`. El backend **nunca** lee el `tenant_id` del body — siempre lo extrae del token verificado.

---

## Índice

1. [Health](#1-health)
2. [Auth — Usuarios](#2-auth--usuarios)
   - 2.1 [Crear usuario](#21-crear-usuario--post-apiv1authusers)
   - 2.2 [Login](#22-login--post-apiv1authlogin)
3. [SaaS Admin — Tenants](#3-saas-admin--tenants)
   - 3.1 [Crear tenant](#31-crear-tenant--post-apiv1saasadmintenants)
4. [Products — Catálogo e Inventario](#4-products--catálogo-e-inventario)
   - 4.1 [Crear producto](#41-crear-producto--post-apiv1products)
   - 4.2 [Listar productos](#42-listar-productos--get-apiv1products)
   - 4.3 [Productos con bajo stock](#43-bajo-stock--get-apiv1productslow-stock)
   - 4.4 [Obtener producto](#44-obtener-producto--get-apiv1productsproduct_id)
   - 4.5 [Actualizar producto](#45-actualizar-producto--patch-apiv1productsproduct_id)
   - 4.6 [Eliminar producto](#46-eliminar-producto--delete-apiv1productsproduct_id)
   - 4.7 [Vender](#47-vender--post-apiv1productsproduct_idsell)
   - 4.8 [Reabastecer](#48-reabastecer--post-apiv1productsproduct_idrestock)
5. [Errores comunes](#5-errores-comunes)
6. [Referencia rápida de tenants y usuarios (entorno local)](#6-referencia-rápida-entorno-local)

---

## 1. Health

### `GET /health`

Liveness probe. No requiere autenticación.

**Respuesta `200`:**
```json
{
  "status": "ok",
  "env": "development"
}
```

---

## 2. Auth — Usuarios

### 2.1 Crear usuario — `POST /api/v1/auth/users`

Crea un usuario y lo asigna a un tenant con un rol. Cubre todos los casos:
- **Bootstrap** (primera vez, sin token): crea el `saas_admin` inicial.
- **saas_admin autenticado**: puede crear `business_admin` en cualquier tenant, o `staff` en cualquier tenant.
- **business_admin autenticado**: puede crear `staff` dentro de su propio tenant.

**Roles válidos:** `saas_admin` · `business_admin` · `staff`

#### Caso A — Bootstrap (sin token, primera vez)
```json
{
  "email": "octavio@kard.com",
  "password": "MiPassword123!",
  "role": "saas_admin",
  "tenant_id": "t_1b9d1e408d35"
}
```

#### Caso B — saas_admin crea un business_admin para un tenant
Header: `Authorization: Bearer <token_saas_admin>`
```json
{
  "email": "admin@tiarosa.example.com",
  "password": "TestPass123!",
  "role": "business_admin",
  "tenant_id": "t_9a40982067c4"
}
```

#### Caso C — business_admin crea un staff en su propio tenant
Header: `Authorization: Bearer <token_business_admin>`
```json
{
  "email": "mesero@tiarosa.example.com",
  "password": "Staff2024!",
  "role": "staff"
}
```
> `tenant_id` se omite — el backend lo toma del token del caller.

**Respuesta `201`:**
```json
{
  "user_id": "u_d30c5f1ac513",
  "email": "admin@tiarosa.example.com",
  "role": "business_admin",
  "tenant_id": "t_9a40982067c4",
  "business_type": "restaurant",
  "is_active": true
}
```

**Reglas de contraseña:** mínimo 8 caracteres, al menos 1 letra y 1 número.

---

### 2.2 Login — `POST /api/v1/auth/login`

Autentica con email y password. No requiere token previo.

**Request:**
```json
{
  "email": "admin@tiarosa.example.com",
  "password": "TestPass123!"
}
```

**Respuesta `200`:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user_id": "u_d30c5f1ac513",
  "tenant_id": "t_9a40982067c4",
  "role": "business_admin",
  "business_type": "restaurant"
}
```

> Guarda el `access_token` para usarlo como `Bearer` en todas las demás rutas.

**Errores:**

| Situación | Código | Error |
|---|---|---|
| Email incorrecto o no existe | `401` | `"Invalid email or password"` |
| Password incorrecta | `401` | `"Invalid email or password"` |
| Cuenta bloqueada por intentos fallidos | `403` | `"Account temporarily locked..."` |

---

## 3. SaaS Admin — Tenants

> Requiere rol `saas_admin`.

### 3.1 Crear tenant — `POST /api/v1/saasadmin/tenants`

Registra un nuevo negocio (tenant). Los usuarios de ese negocio se crean por separado con `POST /api/v1/auth/users`.

Header: `Authorization: Bearer <token_saas_admin>`

**Tipos de negocio válidos:** `restaurant` · `optician` · `platform`

**Request — restaurante:**
```json
{
  "business_name": "Taqueria El Güero",
  "business_type": "restaurant",
  "email": "contacto@elguero.example.com",
  "phone": "+52 55 1234 5678"
}
```

**Request — óptica:**
```json
{
  "business_name": "Óptica Claridad",
  "business_type": "optician",
  "email": "info@claridad.example.com",
  "phone": "+52 33 9876 5432"
}
```

> `phone` es opcional.

**Respuesta `201`:**
```json
{
  "tenant_id": "t_9a40982067c4",
  "business_name": "Taqueria El Güero",
  "business_type": "restaurant",
  "email": "contacto@elguero.example.com",
  "phone": "+52 55 1234 5678",
  "is_active": true
}
```

---

## 4. Products — Catálogo e Inventario

> Requiere autenticación. El `tenant_id` siempre viene del token — el catálogo del caller es el único al que puede acceder.
>
> **Importante:** los tenants con `business_type: platform` no pueden tener productos.

---

### 4.1 Crear producto — `POST /api/v1/products`

**`product_type` válidos:** `insumo` · `final`

#### Restaurant

El metadata describe el **platillo** tal como lo recibe el cliente.

Campos requeridos en `metadata`: `categoria`, `tiempo_preparacion_min`, `picante`
Campo opcional: `alergenos`
`categoria` válidos: `entrada` · `plato_fuerte` · `postre` · `bebida` · `guarnicion`

```json
{
  "product_type": "final",
  "name": "Enchiladas Verdes",
  "description": "Tortillas rellenas bañadas en salsa verde con crema y queso",
  "price": 8.50,
  "inventory_count": 25,
  "low_stock_threshold": 5,
  "metadata": {
    "categoria": "plato_fuerte",
    "tiempo_preparacion_min": 20,
    "picante": true,
    "alergenos": ["lacteos", "gluten"]
  }
}
```

```json
{
  "product_type": "final",
  "name": "Agua de Jamaica",
  "description": "Bebida fresca de flor de jamaica",
  "price": 2.50,
  "inventory_count": 40,
  "low_stock_threshold": 10,
  "metadata": {
    "categoria": "bebida",
    "tiempo_preparacion_min": 2,
    "picante": false
  }
}
```

```json
{
  "product_type": "final",
  "name": "Flan Napolitano",
  "description": "Postre tradicional con caramelo",
  "price": 3.50,
  "inventory_count": 12,
  "low_stock_threshold": 3,
  "metadata": {
    "categoria": "postre",
    "tiempo_preparacion_min": 5,
    "picante": false,
    "alergenos": ["lacteos", "huevo"]
  }
}
```

#### Optician

El metadata describe el **lente terminado** tal como se vende al cliente.

Campos requeridos en `metadata`: `esfera`, `cilindro`, `eje`, `material`
`eje` es entero entre `0` y `180`.

```json
{
  "product_type": "final",
  "name": "Lente Monofocal CR-39",
  "description": "Lente oftalmico monofocal resistente a rayaduras",
  "price": 45.00,
  "inventory_count": 30,
  "low_stock_threshold": 5,
  "metadata": {
    "esfera": "-2.00",
    "cilindro": "-0.50",
    "eje": 180,
    "material": "CR-39"
  }
}
```

```json
{
  "product_type": "final",
  "name": "Lente Bifocal Policarbonato",
  "description": "Lente bifocal de alta resistencia",
  "price": 95.00,
  "inventory_count": 15,
  "low_stock_threshold": 3,
  "metadata": {
    "esfera": "+1.50",
    "cilindro": "0.00",
    "eje": 0,
    "material": "policarbonato"
  }
}
```

**Respuesta `201`:**
```json
{
  "product_id": "p_fccb858d5f7a",
  "tenant_id": "t_9a40982067c4",
  "product_type": "final",
  "name": "Enchiladas Verdes",
  "description": "Tortillas rellenas bañadas en salsa verde con crema y queso",
  "price": "8.50",
  "inventory_count": 25,
  "low_stock_threshold": 5,
  "metadata": {
    "categoria": "plato_fuerte",
    "tiempo_preparacion_min": 20,
    "picante": true,
    "alergenos": ["lacteos", "gluten"]
  },
  "is_deleted": false
}
```

---

### 4.2 Listar productos — `GET /api/v1/products`

Sin body. Parámetros opcionales por query string:

| Param | Tipo | Default | Descripción |
|---|---|---|---|
| `page` | integer ≥ 1 | `1` | Página actual |
| `page_size` | integer 1-100 | `20` | Productos por página |
| `search` | string | — | Filtra por nombre (case-insensitive) |
| `product_type` | `insumo` \| `final` | — | Filtra por tipo de producto |

**Ejemplos:**
```
GET /api/v1/products
GET /api/v1/products?page=1&page_size=10
GET /api/v1/products?search=enchilada
GET /api/v1/products?product_type=final
GET /api/v1/products?search=lente&product_type=final&page=1&page_size=5
```

**Respuesta `200`:**
```json
{
  "items": [
    {
      "product_id": "p_fccb858d5f7a",
      "tenant_id": "t_9a40982067c4",
      "product_type": "final",
      "name": "Enchiladas Verdes",
      "description": "Tortillas rellenas bañadas en salsa verde con crema y queso",
      "price": "8.50",
      "inventory_count": 25,
      "low_stock_threshold": 5,
      "metadata": {
        "categoria": "plato_fuerte",
        "tiempo_preparacion_min": 20,
        "picante": true,
        "alergenos": ["lacteos", "gluten"]
      },
      "is_deleted": false
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

---

### 4.3 Bajo stock — `GET /api/v1/products/low-stock`

Sin body, sin params. Devuelve los productos activos donde `inventory_count < low_stock_threshold`, ordenados de menor a mayor stock.

**Respuesta `200`:**
```json
[
  {
    "product_id": "p_b3b237d676f2",
    "tenant_id": "t_9a40982067c4",
    "product_type": "final",
    "name": "Flan Napolitano",
    "description": "Postre tradicional con caramelo",
    "price": "3.50",
    "inventory_count": 2,
    "low_stock_threshold": 3,
    "metadata": {
      "categoria": "postre",
      "tiempo_preparacion_min": 5,
      "picante": false,
      "alergenos": ["lacteos", "huevo"]
    },
    "is_deleted": false
  }
]
```

---

### 4.4 Obtener producto — `GET /api/v1/products/{product_id}`

Sin body.

```
GET /api/v1/products/p_fccb858d5f7a
```

**Respuesta `200`:** mismo shape que un ítem de la lista.

**Errores:**

| Situación | Código | Error |
|---|---|---|
| ID no existe o fue soft-deleted | `404` | `"Product not found"` |
| ID pertenece a otro tenant | `404` | `"Product not found"` |

---

### 4.5 Actualizar producto — `PATCH /api/v1/products/{product_id}`

Actualización parcial — solo manda los campos que quieras cambiar.
**No se puede modificar `inventory_count` por aquí** — usa `/sell` o `/restock`.
Si mandas `metadata` debe ir **completa** (se reemplaza, no se mezcla).

```
PATCH /api/v1/products/p_fccb858d5f7a
```

**Solo precio:**
```json
{
  "price": 9.50
}
```

**Nombre y umbral de alerta:**
```json
{
  "name": "Enchiladas Verdes Especiales",
  "low_stock_threshold": 8
}
```

**Metadata completa (debe incluir todos los campos requeridos):**
```json
{
  "metadata": {
    "categoria": "plato_fuerte",
    "tiempo_preparacion_min": 25,
    "picante": true,
    "alergenos": ["lacteos", "gluten", "nueces"]
  }
}
```

**Descripción:**
```json
{
  "description": "Tortillas de maíz rellenas de pollo, bañadas en salsa verde"
}
```

**Respuesta `200`:** el producto completo con los cambios aplicados.

---

### 4.6 Eliminar producto — `DELETE /api/v1/products/{product_id}`

Sin body. Marca el producto como `is_deleted: true` — **nunca borra el registro de la base de datos**. El producto desaparece de la lista y del get, pero el historial de transacciones se conserva.

```
DELETE /api/v1/products/p_fccb858d5f7a
```

**Respuesta `204`:** sin body.

> Un producto eliminado con soft-delete **bloquea su nombre** — no podrás crear otro producto con el mismo nombre en el mismo tenant (la restricción única aplica igual sobre registros eliminados).

---

### 4.7 Vender — `POST /api/v1/products/{product_id}/sell`

Decrementa `inventory_count` en `quantity`. Nunca puede quedar por debajo de cero. Genera una fila inmutable en `inventory_transactions` y una entrada en el audit log.

```
POST /api/v1/products/p_fccb858d5f7a/sell
```

```json
{
  "quantity": 3,
  "notes": "Mesa 5 — orden #42"
}
```

> `notes` es opcional.

**Respuesta `200`:**
```json
{
  "product_id": "p_fccb858d5f7a",
  "action": "sell",
  "quantity_change": -3,
  "quantity_before": 25,
  "quantity_after": 22,
  "inventory_count": 22
}
```

**Error si no hay stock suficiente (`400`):**
```json
{
  "success": false,
  "error": "Cannot sell 30 units — only 22 in stock"
}
```

---

### 4.8 Reabastecer — `POST /api/v1/products/{product_id}/restock`

Incrementa `inventory_count` en `quantity`. Genera una fila inmutable en `inventory_transactions` y una entrada en el audit log.

```
POST /api/v1/products/p_fccb858d5f7a/restock
```

```json
{
  "quantity": 20,
  "notes": "Pedido semanal — proveedor Distribuidora Norte"
}
```

> `notes` es opcional.

**Respuesta `200`:**
```json
{
  "product_id": "p_fccb858d5f7a",
  "action": "restock",
  "quantity_change": 20,
  "quantity_before": 22,
  "quantity_after": 42,
  "inventory_count": 42
}
```

---

## 5. Errores comunes

Todos los errores siguen el mismo envelope:
```json
{
  "success": false,
  "error": "Mensaje descriptivo del error"
}
```

| Código | Error | Cuándo ocurre |
|---|---|---|
| `401` | `"Invalid email or password"` | Credenciales incorrectas en login |
| `401` | `"Not authenticated"` | Request sin token o token inválido |
| `403` | `"Access denied"` | El usuario no tiene permiso para esa acción |
| `403` | `"Account temporarily locked..."` | Demasiados intentos fallidos de login |
| `404` | `"Product not found"` | ID inexistente, soft-deleted, o de otro tenant |
| `404` | `"Tenant not found"` | `tenant_id` referenciado no existe o está inactivo |
| `400` | `"A product with this name already exists"` | Nombre duplicado dentro del mismo tenant |
| `400` | `"Cannot sell N units — only M in stock"` | Stock insuficiente para la venta |
| `422` | `"Invalid metadata: <detalle>"` | Metadata no cumple el schema del business_type |
| `422` | Errores de validación Pydantic | Campos faltantes, tipos incorrectos, etc. |

**Errores de validación de metadata por business_type:**

| Caso | Respuesta |
|---|---|
| Campo requerido faltante | `422` `"Invalid metadata: 'campo' is a required property"` |
| Campo extra no permitido | `422` `"Invalid metadata: Additional properties are not allowed ('campo' was unexpected)"` |
| Valor de enum inválido (ej. categoria) | `422` `"Invalid metadata: 'valor' is not one of ['entrada', 'plato_fuerte', ...]"` |
| business_type sin schema (platform) | `422` `"Products are not supported for business_type 'platform'"` |

---

## 6. Referencia rápida (entorno local)

### Tenants

| tenant_id | Nombre | Tipo |
|---|---|---|
| `t_1b9d1e408d35` | KARD | `platform` |
| `t_9a40982067c4` | Tia rosa | `restaurant` |
| `t_e107cda08be3` | Buena vista | `optician` |

### Usuarios

| user_id | Email | Rol | Tenant |
|---|---|---|---|
| `u_0dda7b418275` | `octavio@kard.com` | `saas_admin` | KARD |
| `u_d30c5f1ac513` | `admin@tiarosa.example.com` | `business_admin` | Tia rosa |
| `u_34870bd40f2f` | `admin@buenavistaexample.com` | `business_admin` | Buena vista |

### Schemas de metadata por business_type

#### `restaurant` — platillo listo para el cliente

| Campo | Tipo | Requerido | Valores |
|---|---|---|---|
| `categoria` | string enum | ✅ | `entrada` `plato_fuerte` `postre` `bebida` `guarnicion` |
| `tiempo_preparacion_min` | integer ≥ 0 | ✅ | minutos de preparación |
| `picante` | boolean | ✅ | `true` / `false` |
| `alergenos` | array de strings | ⬜ opcional | ej. `["gluten","lacteos","nueces","mariscos","huevo"]` |

#### `optician` — lente terminado para el cliente

| Campo | Tipo | Requerido | Valores |
|---|---|---|---|
| `esfera` | string | ✅ | ej. `"-2.00"`, `"+1.50"` |
| `cilindro` | string | ✅ | ej. `"-0.50"`, `"0.00"` |
| `eje` | integer 0–180 | ✅ | ej. `180`, `90` |
| `material` | string | ✅ | ej. `"CR-39"`, `"policarbonato"`, `"trivex"` |

> `additionalProperties: false` — cualquier campo extra no listado arriba es rechazado con `422`.
