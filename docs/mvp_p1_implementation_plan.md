# Plan de Implementación — MVP Fase 1: Plataforma B2B2C
**Proyecto:** KARD  
**Arquitectura:** Monolito FastAPI extendido (sin microservicios aún)  
**Objetivo:** Añadir capa B2C pública y operaciones de negocio críticas sin modificar ni romper el backend B2B existente.

---

## Principio Guía

> El B2C es **aditivo**. El `tenant_id` en el lado público se resuelve desde el `slug` de la URL, no desde un JWT. Las reglas de aislamiento multi-tenant se respetan en ambas capas — solo cambia el punto de entrada.

```
B2B:  tenant_id ← JWT verificado       (privado, sin cambios)
B2C:  tenant_id ← slug resuelto en DB  (público, lectura limitada)
```

---

## Estado Actual (No tocar)

- `/api/v1/auth/*` — Auth B2B, JWT, blacklist Redis ✅
- `/api/v1/products/*` — CRUD de inventario B2B ✅
- `/api/v1/saasadmin/*` — Gestión de plataforma ✅
- Multi-tenant isolation por `tenant_id` ✅
- Audit logging ✅
- Repository + Service + Layered Architecture ✅

---

## Pasos de Implementación

### PASO 1 — Migración de Base de Datos: `slug` + `consumer_events`
**Prioridad:** 🔴 Bloqueador (todos los pasos siguientes dependen de esto)

#### Cambios en esquema SQL

**Tabla `tenants` — Añadir slug único:**
```sql
ALTER TABLE tenants
  ADD COLUMN slug VARCHAR(60) UNIQUE,
  ADD COLUMN is_public BOOLEAN DEFAULT FALSE;

CREATE UNIQUE INDEX uq_tenant_slug ON tenants (slug)
  WHERE slug IS NOT NULL;
```
- `slug`: identificador de URL (`papeleria-don-jorge`). Se genera automáticamente desde el nombre del negocio al onboardear.
- `is_public`: flag que el `business_admin` activa cuando quiere exponer su vitrina al público.

**Tabla `consumer_events` — Registro de eventos (se llena desde Paso 3, se procesa en Fase 3):**
```sql
CREATE TABLE consumer_events (
    id           VARCHAR(30)  PRIMARY KEY,
    consumer_id  VARCHAR(30),            -- NULL si el visitante es anónimo
    tenant_id    VARCHAR(30)  NOT NULL,  -- De qué vitrina
    event_type   VARCHAR(50)  NOT NULL,  -- "view_product", "search", "add_to_cart"
    payload      JSONB,
    created_at   TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX idx_consumer_events_consumer ON consumer_events (consumer_id);
CREATE INDEX idx_consumer_events_tenant   ON consumer_events (tenant_id, created_at);
```
> Esta tabla se crea ahora pero no se procesa hasta Fase 3 (motor de IA). El costo de crearla hoy es mínimo; el costo de no tener datos cuando se necesite es enorme.

---

### PASO 2 — Auto-Onboarding de Tenants (Self-Service Registration)
**Prioridad:** 🔴 Bloqueador (sin esto, ningún cliente puede registrarse solo)

#### Módulo afectado: `auth/`

**Nueva ruta pública:**
```
POST /api/v1/auth/register-tenant
```

**Payload esperado:**
```json
{
  "business_name": "Papelería Don Jorge",
  "business_type": "retail",
  "email": "jorge@papeleria.com",
  "password": "••••••••"
}
```

**Lógica en `auth/service.py` (nuevo método `register_tenant`):**
1. Validar que el email no exista en la plataforma.
2. Generar un `slug` único desde `business_name` (ej. `"Papelería Don Jorge"` → `"papeleria-don-jorge"`). Si ya existe, añadir sufijo numérico (`-2`, `-3`…).
3. Crear el registro en `tenants` (estado `pending_payment` o `trial`).
4. Crear el primer usuario con `role = "business_admin"` vinculado a ese tenant.
5. Disparar email de bienvenida + verificación (ver Paso 5).
6. Retornar JWT de sesión para que el admin entre directo al panel.

**Archivos a crear/modificar:**
- [MODIFY] `auth/service.py` — añadir `register_tenant()`
- [MODIFY] `auth/routes.py` — añadir `POST /register-tenant`
- [MODIFY] `auth/models.py` — añadir `TenantRegistrationRequest` (Pydantic)
- [MODIFY] `db/models.py` — añadir campo `slug` e `is_public` al modelo `Tenant`

---

### PASO 3 — Módulo `storefront/` (Vitrina Pública B2C)
**Prioridad:** 🔴 Core del B2C

Este módulo es **completamente nuevo**. No modifica ningún archivo existente del B2B.

#### Estructura de archivos
```
storefront/
  __init__.py
  models.py      ← Pydantic: respuestas públicas (sin campos internos)
  repository.py  ← Resuelve slug → tenant; lee productos públicos
  service.py     ← Lógica: verifica is_public, registra consumer_events
  routes.py      ← Rutas GET públicas, sin auth requerida
```

#### Rutas expuestas
```
GET  /s/{slug}                  → Info pública del negocio
GET  /s/{slug}/products         → Catálogo paginado (con caché Redis)
GET  /s/{slug}/products/{id}    → Detalle de un producto
```

#### Detalle de `storefront/repository.py`
```python
class StorefrontRepository:
    def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """Resuelve slug → tenant. Solo retorna tenants activos y públicos."""
        return (
            self.db.query(Tenant)
            .filter(Tenant.slug == slug, Tenant.is_public.is_(True))
            .first()
        )
```
> Reutiliza `ProductRepository` existente para obtener el catálogo — el mismo código, el mismo aislamiento por `tenant_id`.

#### Detalle de `storefront/service.py`
1. Llama `get_tenant_by_slug(slug)` → si no existe: 404.
2. Llama `ProductRepository.list_products(tenant_id=tenant.id, ...)`.
3. Filtra campos internos (ej. `created_by`, `metadata_` privado si aplica).
4. Registra un evento en `consumer_events` con `event_type = "view_catalog"`.
5. Retorna respuesta pública.

#### Modelos públicos en `storefront/models.py`
Los modelos de respuesta B2C **no exponen** campos internos como `tenant_id`, `created_by` o metadata sensible. Son DTOs distintos al `ProductResponse` del B2B.

---

### PASO 4 — Caché Redis para Catálogo Público
**Prioridad:** 🟡 Necesario antes de lanzar (el catálogo es el endpoint más consultado)

#### Estrategia
- **TTL:** 5 minutos para `GET /s/{slug}/products`.
- **Clave:** `storefront:catalog:{slug}:page:{page}:size:{size}`.
- **Invalidación:** Cuando el `business_admin` crea, edita o elimina un producto en el B2B, el servicio correspondiente llama `redis.delete(f"storefront:catalog:{tenant.slug}:*")`.

#### Archivos a modificar
- [MODIFY] `storefront/service.py` — añadir lógica de caché antes de llamar al repositorio.
- [MODIFY] `products/service.py` — añadir invalidación de caché en `create_product`, `update_product`, `soft_delete_product`.
- [MODIFY] `core/redis_client.py` — añadir helper `invalidate_storefront_cache(slug)`.

---

### PASO 5 — Módulo `consumers/` (Identidad Global del Consumidor)
**Prioridad:** 🟡 Necesario para guardar historial y hacer retención

#### Tabla nueva en DB
```sql
CREATE TABLE consumers (
    id           VARCHAR(30)  PRIMARY KEY,
    email        VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    name         VARCHAR(100),
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT now()
);
-- Sin tenant_id — es una identidad global de plataforma
```

#### Estructura de archivos
```
consumers/
  __init__.py
  models.py      ← Pydantic: ConsumerRegister, ConsumerLogin, ConsumerResponse
  repository.py  ← get_by_email, create, get_by_id
  service.py     ← register(), login() → emite JWT de consumidor
  routes.py      ← POST /consumers/register, POST /consumers/login
```

#### JWT del consumidor (diferente al B2B)
```python
# Claims del token B2B (actual):
{ "user_id": "...", "tenant_id": "...", "role": "...", "business_type": "..." }

# Claims del token B2C (nuevo):
{ "consumer_id": "...", "type": "consumer" }
```
El token de consumidor **no lleva `tenant_id`** — el tenant siempre se resuelve desde el slug de la URL.

#### Rutas expuestas
```
POST /consumers/register   → Crear cuenta global
POST /consumers/login      → Login → JWT de consumidor
GET  /consumers/me         → Perfil propio (requiere JWT consumidor)
```

---

### PASO 6 — Integración de Pagos con Stripe
**Prioridad:** 🟡 Necesario para monetizar (puede ir después del lanzamiento inicial en beta)

#### Flujo completo
```
1. Tenant elige plan en el frontend
        ↓
2. Backend crea Stripe Checkout Session
   POST /api/v1/billing/checkout
        ↓
3. Stripe redirige al tenant tras el pago
        ↓
4. Stripe envía Webhook → POST /api/v1/billing/webhook
        ↓
5. Backend verifica firma del webhook
   → Actualiza tenant.subscription_status = "active"
   → Actualiza tenant.plan = "starter" | "pro" | "enterprise"
```

#### Cambios en DB
```sql
ALTER TABLE tenants
  ADD COLUMN stripe_customer_id    VARCHAR(100),
  ADD COLUMN stripe_subscription_id VARCHAR(100),
  ADD COLUMN subscription_status   VARCHAR(30) DEFAULT 'trial',
  ADD COLUMN plan                  VARCHAR(30) DEFAULT 'trial',
  ADD COLUMN trial_ends_at         TIMESTAMPTZ;
```

#### Módulo nuevo `billing/`
```
billing/
  __init__.py
  service.py     ← create_checkout_session(), handle_webhook()
  routes.py      ← POST /api/v1/billing/checkout
                    POST /api/v1/billing/webhook  (sin auth, firma Stripe)
```

#### Enforcement de plan en el B2B
En `products/service.py`, antes de crear un producto, verificar límite del plan:
```python
# Ejemplo: Plan "starter" → máximo 100 productos activos
if tenant.plan == "starter":
    count = self.products.count_active(current_user.tenant_id)
    if count >= 100:
        raise PlanLimitExceededError("Upgrade your plan to add more products")
```

---

### PASO 7 — Emails Transaccionales
**Prioridad:** 🟡 Necesario para retención y operación básica

**Proveedor recomendado:** Resend (API simple, buena entregabilidad, tier gratis generoso)

#### Emails a implementar
| Trigger | Destinatario | Contenido |
|---|---|---|
| `register_tenant` | `business_admin` | Bienvenida + verificación de email |
| `consumer/register` | Consumidor | Bienvenida + verificación |
| `forgot-password` | Cualquier usuario | Link de reset con token firmado |
| Stock bajo detectado | `business_admin` | Lista de productos bajo umbral |
| Pago procesado | `business_admin` | Confirmación de suscripción |
| Pago fallido | `business_admin` | Alerta + link para actualizar método |

#### Módulo nuevo `notifications/`
```
notifications/
  __init__.py
  email.py    ← send_email(to, subject, template, context)
              ← Usa Resend SDK o httpx directo a la API
  templates/  ← Templates HTML de emails
```

El módulo `notifications/` es llamado desde los servicios correspondientes. Es una dependencia de salida, no tiene rutas propias.

---

## Mapa de Dependencias entre Pasos

```
PASO 1 (DB Migrations)
    ├── PASO 2 (Onboarding)   ← requiere slug en tenants
    ├── PASO 3 (Storefront)   ← requiere slug en tenants
    │       └── PASO 4 (Caché Redis)  ← requiere storefront
    ├── PASO 5 (Consumers)    ← requiere tabla consumers
    ├── PASO 6 (Stripe)       ← requiere campos billing en tenants
    └── PASO 7 (Emails)       ← requiere todos los anteriores para los triggers
```

---

## Archivos del Proyecto — Resumen de Cambios

### Archivos existentes modificados (cambios menores y aditivos)
| Archivo | Qué cambia |
|---|---|
| `db/models.py` | Añadir campos a `Tenant`; nueva tabla `Consumer`; nueva tabla `consumer_events` |
| `auth/service.py` | Nuevo método `register_tenant()` |
| `auth/routes.py` | Nueva ruta `POST /register-tenant` |
| `auth/models.py` | Nuevo schema `TenantRegistrationRequest` |
| `products/service.py` | Añadir invalidación de caché; validación de límites de plan |
| `core/redis_client.py` | Helper `invalidate_storefront_cache()` |
| `core/exceptions.py` | Nueva excepción `PlanLimitExceededError` |
| `main.py` | Registrar nuevos routers |

### Módulos nuevos (no rompen nada existente)
| Módulo | Descripción |
|---|---|
| `storefront/` | Vitrina pública B2C por slug |
| `consumers/` | Identidad global de consumidores |
| `billing/` | Stripe Checkout + Webhooks |
| `notifications/` | Envío de emails transaccionales |

---

## Lo que NO se implementa en esta fase (intencionalmente)

| Feature | Razón de defer |
|---|---|
| Carrito de compras y órdenes | Requiere decisiones de negocio sobre inventario B2C vs B2B |
| Microservicios / separación de contenedores | No hay carga real que lo justifique aún |
| Motor de IA / embeddings / Lookalike Audiences | Requiere volumen de datos y es Fase 3 |
| Dashboard de analíticas para tenants | Fase 2 |
| App móvil | Fuera de alcance |

---

## Métricas de Éxito del MVP

- [ ] Un negocio puede registrarse, pagar y activar su cuenta sin intervención manual.
- [ ] El catálogo público de un tenant es accesible en `app.com/s/{slug}/products`.
- [ ] Un consumidor puede crear cuenta global y navegar entre vitrinas.
- [ ] El catálogo público responde en < 100ms (gracias a caché Redis).
- [ ] Los eventos de navegación quedan registrados en `consumer_events` (base para Fase 3).
- [ ] Un pago fallido bloquea el acceso B2B del tenant automáticamente.
