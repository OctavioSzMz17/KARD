# KARD — Documentación técnica

**Versión del código:** commit `f7283ac` (main) · **Generado:** 2026-08-04
**Alcance:** backend completo. El frontend vive en otro repositorio.

> Este documento describe **lo que hoy existe en el código**, verificado archivo por archivo.
> Documentos complementarios: [Arquitectura_General.md](reportes/Arquitectura_General.md) (guía de orientación),
> [API Reference.md](API%20Reference.md) (contrato detallado), [Rules.md](../.agents/rules/Rules.md) (reglas de desarrollo).

---

## 1. Qué es KARD

Plataforma **SaaS marketplace B2B2C** con tres actores:

| Actor | Qué hace |
|---|---|
| **Plataforma (KARD)** | Administra tenants, cobra suscripciones |
| **Negocios (tenants)** | Restaurantes y ópticas: administran catálogo, inventario y su tienda pública |
| **Clientes (consumers)** | Descubren tiendas en el marketplace, arman carrito y compran |

```
Negocio se registra → configura su tienda → administra productos e inventario
                                                        ↓
Cliente entra a KARD → explora categorías → elige tienda → carrito → pedido
```

El backend es una **API REST JSON pura**, sin plantillas ni vistas.

---

## 2. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.11 |
| Framework | FastAPI |
| ORM | SQLAlchemy 2.0 |
| Validación | Pydantic v2 + jsonschema (para JSONB) |
| Base de datos | PostgreSQL 15 (Neon en la nube) |
| Caché / carrito / lockouts | Redis 7 |
| Autenticación | JWT (python-jose) + bcrypt vía passlib (12 rounds) |
| Emails | Resend |
| Pagos | Stripe — **skeleton, sin implementar** |
| Contenedores | Docker + Docker Compose |

Nota de dependencias: `bcrypt` está fijado a `<4.1` porque el self-test de passlib 1.7.4 truena con versiones posteriores.

---

## 3. Arquitectura por capas

Todos los módulos siguen el mismo patrón de cuatro archivos:

```
routes.py       →  Solo HTTP: parsea el request, delega al service, devuelve la respuesta.
     ↓
service.py      →  TODA la lógica: reglas, permisos, validaciones, transacciones.
     ↓
repository.py   →  Solo queries a la base. Sin reglas ni commits.
     ↓
models.py       →  Schemas Pydantic (request/response) del módulo.
```

**Dónde tocar cada cosa:**
- Cambiar comportamiento o reglas → `service.py` del módulo.
- Agregar un endpoint → `routes.py` del módulo **y** registrarlo en [main.py](../main.py).
- Cambiar qué campos entran/salen → `models.py` del módulo.
- Cambiar una consulta a la base → `repository.py` del módulo.

---

## 4. Estructura del proyecto

```
Kard/
├── main.py                   ← entrypoint: routers + 22 exception handlers + /health
├── docker-compose.yml        ← backend + postgres + redis
├── kard_database.sql         ← schema fuente de verdad (tablas, enums, constraints)
├── requirements.txt
│
├── core/                     ← INFRAESTRUCTURA COMPARTIDA
│   ├── config.py             ← settings desde .env (singleton, sin valores hardcodeados)
│   ├── security.py           ← hash bcrypt + crear/decodificar JWT
│   ├── dependencies.py       ← get_current_user, get_current_consumer, require_role
│   ├── redis_client.py       ← cliente Redis tolerante a fallos + invalidación de caché
│   ├── tenant_config.py      ← helpers del JSONB `config` del tenant
│   └── exceptions.py         ← todas las excepciones de dominio
│
├── db/
│   ├── database.py           ← engine SQLAlchemy + get_db (sesión por request)
│   └── models.py             ← 10 modelos ORM
│
│  ─── MÓDULOS B2B (negocio) ───
├── auth/                     ← login, registro de tenant, creación de usuarios
├── products/                 ← CRUD de catálogo + inventario + validación JSONB + mcp_tools
├── recipes/                  ← recetas (BOM): platillo ↔ insumos
├── images/                   ← galería de imágenes por producto
├── settings/                 ← branding y perfil público del negocio
├── saasadmin/                ← alta de tenants por el super admin
├── billing/                  ← Stripe (SKELETON — sin lógica)
├── businessadmin/            ← VACÍO (sus funciones quedaron en settings/products)
│
│  ─── MÓDULOS B2C (cliente) ───
├── storefront/               ← tienda pública por slug
├── explore/                  ← descubrimiento: categorías + listado de tiendas
├── consumers/                ← identidad global del cliente
├── cart/                     ← carrito (vive en Redis)
├── orders/                   ← pedidos con descuento de stock en cascada
│
│  ─── TRANSVERSALES ───
├── notifications/            ← emails transaccionales (Resend)
├── audit_logging/            ← audit.py (operaciones) + logger.py (setup)
├── utils/helpers.py          ← generate_id()
│
├── migrations/               ← 4 migraciones SQL, se corren a mano en Neon
├── seeders/                  ← 3 scripts de datos demo
├── tests/                    ← VACÍO (archivos placeholder de 1 línea)
└── docs/                     ← esta documentación + reportes por paso
```

---

## 5. Los dos mundos: B2B vs B2C

Es la distinción más importante del diseño. Son **identidades separadas con tokens no intercambiables**.

| | B2B (negocio) | B2C (cliente) |
|---|---|---|
| Tabla | `users` | `consumers` |
| Login | `POST /api/v1/auth/login` | `POST /api/v1/consumers/login` |
| Claims del JWT | `user_id`, `tenant_id`, `role`, `business_type` | `consumer_id`, `type: "consumer"` |
| Dependency | `get_current_user` | `get_current_consumer` |
| De dónde sale el `tenant_id` | del JWT verificado | del **slug** en la URL |
| Alcance | un solo negocio | toda la plataforma |

El discriminador `type: "consumer"` impide que un token B2B toque rutas B2C y viceversa
([core/dependencies.py:182](../core/dependencies.py:182)).

Además, `get_current_user` **recarga el usuario desde la base en cada request**: si una cuenta se
desactiva, pierde acceso de inmediato aunque su token siga vigente.

---

## 6. Modelo de datos (10 tablas)

Fuente de verdad: [kard_database.sql](../kard_database.sql). Modelos ORM en [db/models.py](../db/models.py).

### Enums de PostgreSQL
- `business_type_enum`: `restaurant` · `optician` · `platform`
- `user_role_enum`: `saas_admin` · `business_admin` · `staff`
- `product_type_enum`: `insumo` · `final`
- `inventory_action_enum`: `sell` · `restock` · `adjust` · `create`

### Tablas

| Tabla | Descripción | Campos clave |
|---|---|---|
| `tenants` | El negocio. Raíz del aislamiento multi-tenant. | `slug` (único), `is_public`, `config` (JSONB con branding/perfil), `business_type` |
| `users` | Usuario B2B, siempre de un solo tenant. | `tenant_id`, `role`, `password_hash`, `is_active`, `last_login` |
| `products` | Catálogo. | `product_type`, `price`, `inventory_count`, `low_stock_threshold`, `metadata` (JSONB), `is_deleted` |
| `inventory_transactions` | Bitácora **inmutable** de cada movimiento de stock. | `action`, `quantity_before/change/after`, CHECK que valida la aritmética |
| `product_recipes` | Lista de materiales: un platillo consume N de cada insumo. | `final_product_id`, `insumo_product_id`, `quantity` |
| `images` | Galería polimórfica (`owner_type`/`owner_id`). Hoy solo productos. | `sort_order` (0 = principal), `url`, `alt_text` |
| `consumers` | Identidad B2C **global** — deliberadamente sin `tenant_id`. | `email` único, `hashed_password` |
| `consumer_events` | Eventos de comportamiento; `consumer_id` nulo para anónimos. | `event_type`, `payload` (JSONB) — insumo de la IA de Fase 3 |
| `orders` | Pedido de un consumer en un tenant. | `status`: `pending → paid → fulfilled \| canceled` |
| `order_items` | Línea de pedido con **precio congelado** al momento de la compra. | `product_name`, `unit_price`, `subtotal` |

### Metadata JSONB por tipo de negocio

El esquema depende del par **(business_type, product_type)** y se valida con `jsonschema` en la capa
de servicio ([products/models.py:99](../products/models.py:99)). `additionalProperties: false` rechaza
cualquier campo no declarado.

| Combinación | Campos requeridos | Opcionales |
|---|---|---|
| restaurant + **final** (platillo) | `categoria` (entrada/plato_fuerte/postre/bebida/guarnicion), `tiempo_preparacion_min`, `picante` | `alergenos[]` |
| restaurant + **insumo** | `unidad_medida`, `perecedero`, `dias_expiracion` | — |
| optician + final/insumo | `esfera`, `cilindro`, `eje` (0–180), `material` | — |

`business_type = platform` no admite productos: devuelve 422.

---

## 7. Catálogo de endpoints (41 + health)

Prefijo general `/api/v1`, salvo el storefront público que usa `/s`.
🔒 = requiere JWT de negocio · 👤 = requiere JWT de consumer · 🌐 = público

### Autenticación — `/api/v1/auth`
| Método | Ruta | Acceso | Descripción |
|---|---|---|---|
| POST | `/register-tenant` | 🌐 | Registro self-service: crea tenant + primer `business_admin` + slug y devuelve JWT listo (201) |
| POST | `/users` | 🔒/🌐 | Crea usuario asignado a tenant + rol. Anónimo solo para el bootstrap único del `saas_admin` (201) |
| POST | `/login` | 🌐 | Autenticación por email/password |

**Reglas de creación de usuarios** ([auth/service.py:252](../auth/service.py:252)):
un `business_admin` solo puede crear usuarios **en su propio tenant** (el `tenant_id` sale del JWT, nunca
del body); un `saas_admin` puede elegir cualquier tenant activo; el primer `saas_admin` se crea con el
bootstrap anónimo, que se cierra automáticamente en cuanto existe uno.

**Protección de fuerza bruta:** 5 intentos fallidos → bloqueo de 15 minutos, con contadores en Redis
(`auth:failed:{email}`, `auth:lockout:{email}`). Cada intento se registra con IP en la auditoría.

### Catálogo e inventario — `/api/v1/products` 🔒
| Método | Ruta | Descripción |
|---|---|---|
| POST | `` | Crear producto; valida metadata JSONB según el giro (201) |
| GET | `` | Listar con paginación (`page`, `page_size` ≤100), búsqueda por nombre y filtro `product_type` |
| GET | `/low-stock` | Productos bajo su umbral de alerta (widget del dashboard) |
| GET | `/{product_id}` | Detalle, con imágenes incluidas |
| PATCH | `/{product_id}` | Actualización parcial. **No** permite tocar `inventory_count` |
| DELETE | `/{product_id}` | Soft delete (`is_deleted = true`), nunca borrado físico (204) |
| POST | `/{product_id}/sell` | Descuenta stock, nunca por debajo de cero |
| POST | `/{product_id}/restock` | Incrementa stock (llegó mercancía) |

El stock **solo** cambia por `/sell` y `/restock`, para que la bitácora `inventory_transactions` nunca
quede inconsistente.

### Recetas — `/api/v1/products` 🔒
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/{product_id}/recipe` | Ver ingredientes de un producto final |
| POST | `/{product_id}/recipe/items` | Agregar ingrediente o actualizar su cantidad |
| DELETE | `/{product_id}/recipe/items/{insumo_id}` | Quitar ingrediente |

Valida que el destino sea `final`, el ingrediente sea `insumo` y que no haya auto-referencia (400).

### Imágenes — `/api/v1/products` 🔒
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/{product_id}/images` | Agregar imagen a la galería (201) |
| GET | `/{product_id}/images` | Listar ordenadas por `sort_order` |
| PATCH | `/{product_id}/images/{image_id}` | Actualizar url / alt_text / orden |
| DELETE | `/{product_id}/images/{image_id}` | Borrado físico — los assets no tienen soft delete (204) |

### Configuración de tienda — `/api/v1/settings` 🔒
| Método | Ruta | Descripción |
|---|---|---|
| GET / PUT | `/branding` | `logo_url`, `banner_url`, `theme.{primary,accent}` (hex `#RRGGBB` validado) |
| GET / PUT | `/profile` | `tagline`, `description`, `contact.{address,phone,email,whatsapp,maps_url,hours[],social{facebook,instagram,tiktok,x,youtube}}` |

Ambos PUT son **reemplazo total**: lo que se omite queda en null. Todas las URLs deben empezar con
`http://` o `https://`.

### Administración de la plataforma — `/api/v1/saasadmin` 🔒 (solo `saas_admin`)
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/tenants` | Alta de un negocio nuevo (201) |

### Suscripciones — `/api/v1/billing` ⚠️ **NO IMPLEMENTADO**
| Método | Ruta | Estado |
|---|---|---|
| POST | `/checkout` | `NotImplementedError` |
| POST | `/webhook` | `NotImplementedError` |
| GET | `/plan` | `NotImplementedError` |

### Tienda pública — `/s` 🌐
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/{slug}` | Info pública del negocio (branding + perfil) |
| GET | `/{slug}/products` | Catálogo paginado — **solo productos `final`** |
| GET | `/{slug}/products/{product_id}` | Detalle público de un producto |

Cacheado en Redis con TTL de 5 minutos; se invalida automáticamente en cada mutación de productos.

### Descubrimiento — `/api/v1/explore` 🌐
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/categories` | Tipos de negocio con al menos una tienda pública activa (nunca expone `platform`) |
| GET | `` | Listado paginado de tiendas, filtrable por `category` y `search` |

### Clientes — `/api/v1/consumers`
| Método | Ruta | Acceso | Descripción |
|---|---|---|---|
| POST | `/register` | 🌐 | Alta de cliente (201) |
| POST | `/login` | 🌐 | Autenticación de cliente |
| GET | `/me` | 👤 | Perfil del cliente autenticado |

### Carrito — `/api/v1/cart` 👤
| Método | Ruta | Descripción |
|---|---|---|
| POST | `/{slug}/items` | Agregar o actualizar producto (`quantity = 0` lo elimina) |
| GET | `/{slug}` | Ver carrito de esa tienda |
| DELETE | `/{slug}/items/{product_id}` | Quitar un producto |
| DELETE | `/{slug}` | Vaciar el carrito (204) |

El carrito es **un carrito por tienda**: la clave en Redis es `cart:{consumer_id}:{slug}` con TTL de 7 días.

### Pedidos — `/api/v1/orders` 👤
| Método | Ruta | Descripción |
|---|---|---|
| POST | `` | Convierte el carrito en pedido (201) |
| GET | `` | Mis pedidos en todas las tiendas, paginado |
| GET | `/{order_id}` | Detalle; pedidos ajenos devuelven 404 |

### Salud
| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Liveness probe: `{"status": "ok", "env": "..."}` |

---

## 8. El checkout: transacción atómica con cascada

Es la pieza más delicada del sistema ([orders/service.py](../orders/service.py)). Ocurre en dos fases
dentro de una sola transacción de base de datos:

**Fase 1 — análisis con bloqueo de filas.** Por cada ítem del carrito hace `SELECT ... FOR UPDATE`
sobre el producto final **y sobre cada insumo de su receta**. El bloqueo a nivel de fila evita que dos
compradores simultáneos vendan la última unidad (condición de carrera clásica). Clasifica cada ítem
como surtible o no, y en este último caso distingue el motivo: `product_shortage`, `ingredient_shortage`,
`product_and_ingredient` o `unavailable`.

**Fase 2 — aplicación.** Descuenta el stock del producto final, descuenta **en cascada** cada insumo
por `receta.quantity × unidades_vendidas`, escribe un `InventoryTransaction` por cada movimiento
(con nota indicando de qué venta proviene), y crea la orden con sus líneas a precio congelado.

El carrito en Redis se borra **solo después** del commit exitoso, para que un fallo nunca deje al
cliente sin carrito y sin pedido.

**Manejo de faltantes** — lo decide el cliente vía `on_insufficient_stock`:
- `"reject"` (default): `rollback()` que libera los locks sin cambiar nada, y responde **409** con un
  cuerpo procesable por el frontend:
  ```json
  {
    "success": false,
    "code": "INSUFFICIENT_STOCK",
    "error": "Algunos productos no tienen stock suficiente",
    "details": {
      "all_unavailable": false,
      "unavailable": [{
        "product_id": "...", "product_name": "...", "requested": 3,
        "reason": "ingredient_shortage",
        "missing_ingredients": [{"insumo_name": "Tomate", "required": 6, "available": 2, "unit": "kg"}]
      }]
    }
  }
  ```
- `"skip"`: crea el pedido omitiendo lo faltante y lo reporta en `skipped[]`. Si **nada** es surtible,
  responde 409 con `all_unavailable: true`.

---

## 9. Seguridad y aislamiento multi-tenant

| Mecanismo | Implementación |
|---|---|
| Contraseñas | bcrypt con 12 rounds ([core/security.py](../core/security.py)). Un hash corrupto se trata como "no coincide", nunca como error 500 |
| `tenant_id` | **Jamás** se lee del request. Sale del JWT verificado (B2B) o del slug (B2C) |
| Recursos ajenos | Devuelven **404**, indistinguible de "no existe" — no se filtra la existencia de datos de otros |
| Revocación | Blacklist de tokens en Redis (`auth:blacklist:{token}`), consultada en cada request |
| Cuentas desactivadas | `get_current_user` recarga de la base: pierden acceso al instante |
| Fuerza bruta | 5 intentos → 15 min de bloqueo, contadores en Redis |
| Autorización por rol | `require_role("saas_admin")` como dependency reutilizable |
| Fuga de información en login | Email inexistente, password incorrecto y cuenta inactiva devuelven el mismo 401 |
| Errores | Mensajes genéricos al usuario; el detalle solo va a logs |

---

## 10. Uso de Redis (4 funciones)

| Uso | Clave | TTL | Si Redis cae |
|---|---|---|---|
| **Carrito de compras** | `cart:{consumer_id}:{slug}` | 7 días | **503** — el carrito *es* Redis |
| Caché del catálogo público | `storefront:catalog:{slug}:*` | 5 min | Lee de Postgres |
| Contadores de login fallido | `auth:failed:{email}` / `auth:lockout:{email}` | 15 min | Sin bloqueo, sigue operando |
| Blacklist de logout | `auth:blacklist:{token}` | vida del token | Sin revocación, sigue operando |

El cliente ([core/redis_client.py](../core/redis_client.py)) es **lazy y tolerante a fallos**: si no puede
conectar, loguea un warning, marca el servicio como no disponible y la API sigue funcionando. La única
excepción deliberada es el carrito.

---

## 11. Manejo de errores

22 excepciones de dominio en [core/exceptions.py](../core/exceptions.py), traducidas a HTTP por handlers
centralizados en [main.py](../main.py). Formato uniforme: `{"success": false, "error": "mensaje"}`.

| Código | Cuándo |
|---|---|
| 400 | Email duplicado, nombre de producto repetido, stock insuficiente en venta directa, receta inválida, carrito vacío |
| 401 | Credenciales inválidas, token ausente/expirado/revocado |
| 402 | Límite del plan excedido |
| 403 | Acceso cross-tenant, cuenta bloqueada, rol insuficiente |
| 404 | Producto / imagen / tenant / storefront / pedido / consumer no encontrado (incluye "es de otro dueño") |
| 409 | Conflicto de stock en checkout (con detalle estructurado) |
| 422 | Metadata JSONB que no cumple el esquema del giro |
| 503 | Redis caído durante una operación de carrito |

---

## 12. Notificaciones por email

Módulo `notifications` con Resend. Cinco plantillas HTML implementadas: `tenant_welcome`,
`consumer_welcome`, `low_stock_alert`, `payment_confirmed`, `payment_failed`. Los envíos son
*fire-and-forget*: un fallo de email nunca tumba la operación de negocio. Si `RESEND_API_KEY` está
vacío, el envío se desactiva de forma segura (útil en desarrollo local).

---

## 13. Configuración (.env)

Todo se lee vía [core/config.py](../core/config.py). **Nada está hardcodeado** — `DATABASE_URL`,
`JWT_SECRET_KEY`, `REDIS_URL` y `CORS_ORIGINS` son obligatorias y el arranque falla si faltan.

| Variable | Para qué |
|---|---|
| `DATABASE_URL` | Conexión a Postgres (Neon o Docker local) |
| `JWT_SECRET_KEY` / `JWT_ALGORITHM` / `JWT_EXPIRATION_HOURS` | Firma y vigencia de tokens (default HS256, 24 h) |
| `REDIS_URL` | Carrito, caché, lockouts |
| `CORS_ORIGINS` | Orígenes permitidos, separados por coma |
| `RESEND_API_KEY` / `EMAIL_FROM` / `EMAIL_FROM_NAME` | Emails (vacío = desactivado) |
| `APP_URL` | Base de los links en los correos |
| `ENVIRONMENT` / `LOG_LEVEL` | Entorno y verbosidad |
| `PORT_BACKEND` / `PORT_DB` / `PORT_REDIS` | Mapeo de puertos al host |

---

## 14. Cómo levantar el proyecto

```bash
docker compose up -d
```

Tras cambiar código Python:

```bash
docker compose up -d --build backend
```

- API: `http://localhost:8000`
- Swagger interactivo: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

### Cambios de esquema en la base

No hay Alembic. El flujo es manual:
1. La verdad del esquema completo está en [kard_database.sql](../kard_database.sql).
2. Los cambios incrementales van en `migrations/NNN_descripcion.sql`.
3. Se ejecutan **a mano en el SQL Editor de Neon**.
4. Se actualiza el modelo ORM en [db/models.py](../db/models.py).

Migraciones existentes: `001` (slug + consumer_events), `002` (consumers), `003` (orders), `004` (recetas).

### Datos de demo
`seeders/seed_optica_demo.py`, `seed_restaurant_insumos.py`, `seed_stock_and_recipes.py`.

---

## 15. Herramientas MCP (preparadas, no conectadas)

[products/mcp_tools.py](../products/mcp_tools.py) declara la *forma* de 5 herramientas para agentes de IA:
`create_product`, `list_products`, `get_product`, `update_product`, `sell_product`.

Deliberadamente **excluidas** por seguridad: `delete_product` (destructivo aunque sea soft) y
`restock_product` (decisión humana). El módulo solo registra definiciones — no lo importa ni ejecuta la
app; conectarlo a un runtime MCP es un paso posterior.

---

## 16. Estado del proyecto

### Implementado y funcionando ✅
Autenticación B2B con roles y lockout · registro self-service de tenants · CRUD completo de catálogo
con validación JSONB por giro · inventario con bitácora inmutable · recetas con descuento en cascada ·
galería de imágenes · branding y perfil de tienda · storefront público con caché · descubrimiento de
tiendas · identidad de clientes · carrito en Redis · checkout atómico con bloqueo de filas · emails
transaccionales · auditoría · Docker Compose.

### Pendiente ⚠️

| # | Qué falta | Impacto |
|---|---|---|
| 1 | **Stripe / billing** — los 3 endpoints lanzan `NotImplementedError` | Los pedidos nunca pasan de `pending` a `paid`. Es el bloqueante real del MVP |
| 2 | **Tests** — `tests/` existe pero todos los archivos están vacíos | Cero cobertura, pese a que Rules.md §15 exige pruebas de aislamiento cross-tenant |
| 3 | **Dashboard de métricas** (US-009 a US-011) | No hay endpoints de agregados: totales, ventas por categoría, tendencias |
| 4 | **`businessadmin/`** — módulo esqueleto | Sin impacto: sus funciones viven en `settings` y `products` |
| 5 | **`utils/validators.py`** — solo un comentario | Sin impacto: la validación real vive en los `models.py` |
| 6 | Verificar que la migración `003` esté aplicada en Neon | Si falta, los pedidos fallan en tiempo de ejecución |

### Recomendación de siguiente paso

Los **tests de aislamiento multi-tenant** son la prioridad técnica: protegen la promesa central del
producto (que un negocio jamás vea datos de otro) y hoy no hay nada que impida que una regresión la
rompa en silencio. Después de eso, Stripe para cerrar el ciclo comercial del MVP.
