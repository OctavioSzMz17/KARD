# Reporte de Implementación — PASO 1: Migración B2B2C
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 1 de 7 — Migración de Base de Datos  
**Fecha:** 2026-06-18  
**Estado:** ✅ Completado y verificado en producción (Neon)

---

## Resumen Ejecutivo

Se extendió el schema de la base de datos para soportar la capa B2C pública sin modificar ningún módulo de negocio existente. Los tenants actuales no se vieron afectados: sus nuevos campos tienen valores seguros por defecto (`slug = NULL`, `is_public = FALSE`).

---

## Cambios en Base de Datos

### Tabla `tenants` — Columnas añadidas

| Columna | Tipo | Default | Nullable | Propósito |
|---|---|---|---|---|
| `slug` | `VARCHAR(60)` | `NULL` | Sí | Identificador URL de la vitrina pública |
| `is_public` | `BOOLEAN` | `FALSE` | No | Controla si la vitrina es visible al público |

#### Estado actual de tenants en producción

| id | business_name | business_type | slug | is_public | is_active |
|---|---|---|---|---|---|
| `t_1b9d1e408d35` | KARD | platform | `NULL` | `false` | `true` |
| `t_9a40982067c4` | Tia rosa | restaurant | `NULL` | `false` | `true` |
| `t_e107cda08be3` | Buena vista | optician | `NULL` | `false` | `true` |

> Los tres tenants existentes mantienen `slug = NULL` e `is_public = FALSE`. Su operación B2B no cambia en absoluto — el slug solo se asigna cuando el tenant decide exponer su vitrina pública.

---

### Tabla nueva: `consumer_events`

Registra cada acción de un visitante/consumidor en las vitrinas públicas. Esta tabla **se llena desde el Paso 3** (storefront) pero **se procesa hasta la Fase 3** (motor de IA). Crearla ahora garantiza que los datos de comportamiento se acumulen desde el primer día.

```
consumer_events
├── id           VARCHAR(30)   PK
├── consumer_id  VARCHAR(30)   nullable  ← NULL para visitantes anónimos
├── tenant_id    VARCHAR(30)   NOT NULL  → FK tenants.id
├── event_type   VARCHAR(50)   NOT NULL  ← 'view_catalog' | 'view_product' | 'search'
├── payload      JSONB         nullable  ← datos específicos del evento
└── created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
```

**Registros actuales:** `0` (esperado — el storefront aún no existe)

---

## Índices Creados

| Índice | Tabla | Tipo | Columnas | Condición |
|---|---|---|---|---|
| `uq_tenant_slug` | `tenants` | UNIQUE parcial | `slug` | `WHERE slug IS NOT NULL` |
| `tenants_slug_key` | `tenants` | UNIQUE | `slug` | — |
| `idx_consumer_events_consumer` | `consumer_events` | btree parcial | `consumer_id` | `WHERE consumer_id IS NOT NULL` |
| `idx_consumer_events_tenant` | `consumer_events` | btree | `tenant_id, created_at DESC` | — |

> El índice parcial `uq_tenant_slug` excluye filas con `slug = NULL`, lo que permite que múltiples tenants no configurados coexistan sin conflicto de unicidad.

---

## Archivos Modificados

### 1. `migrations/001_mvp_phase1_slug_consumer_events.sql` ← nuevo
Script ejecutado contra Neon el 2026-06-18. Contiene los `ALTER TABLE` e `CREATE TABLE` en orden seguro. Sirve como registro permanente de la migración aplicada.

```sql
ALTER TABLE tenants
  ADD COLUMN slug      VARCHAR(60) UNIQUE,
  ADD COLUMN is_public BOOLEAN     NOT NULL DEFAULT FALSE;

CREATE UNIQUE INDEX uq_tenant_slug ON tenants (slug) WHERE slug IS NOT NULL;

CREATE TABLE consumer_events (
    id           VARCHAR(30)  PRIMARY KEY,
    consumer_id  VARCHAR(30),
    tenant_id    VARCHAR(30)  NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    event_type   VARCHAR(50)  NOT NULL,
    payload      JSONB,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

### 2. `kard_database.sql` ← modificado
Fuente de verdad del schema completo actualizada. Incluye las nuevas columnas en la definición de `tenants` y la tabla `consumer_events` al final del archivo con sus COMMENTs e índices.

### 3. `db/models.py` ← modificado

**Modelo `Tenant` — campos añadidos:**
```python
slug      = Column(String(60),  unique=True, nullable=True)
is_public = Column(Boolean,     nullable=False, default=False)
```

**Modelo `ConsumerEvent` — nuevo:**
```python
class ConsumerEvent(Base):
    __tablename__ = "consumer_events"

    id          = Column(String(30), primary_key=True)
    consumer_id = Column(String(30), nullable=True, index=True)
    tenant_id   = Column(String(30), ForeignKey("tenants.id"), nullable=False, index=True)
    event_type  = Column(String(50), nullable=False)
    payload     = Column(JSONB,      nullable=True)
    created_at  = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
```

---

## Flujo: cómo se usa el slug en el B2C

```
ANTES (solo B2B):
  Request → JWT → tenant_id → datos del tenant

DESPUÉS (B2B2C):
  Request B2B → JWT           → tenant_id → datos privados   (sin cambios)
  Request B2C → URL /s/{slug} → tenant_id → catálogo público (nuevo)
```

El `tenant_id` siempre es el mismo — solo cambia de dónde se resuelve. El aislamiento multi-tenant se mantiene en ambas capas.

---

## Flujo del slug: ciclo de vida

```
Registro del tenant (Paso 2)
        │
        ▼
  slug = slugify("Tia Rosa")
        │
        ▼
  "tia-rosa" disponible? ──── No ──→ "tia-rosa-2", "tia-rosa-3"...
        │ Sí
        ▼
  tenants.slug = "tia-rosa"
  tenants.is_public = FALSE          ← vitrina creada pero no visible aún
        │
        ▼
  business_admin activa vitrina
        │
        ▼
  tenants.is_public = TRUE           ← GET /s/tia-rosa ahora responde
```

---

## Impacto en módulos existentes

| Módulo | Impacto |
|---|---|
| `auth/` | Ninguno — login/logout no cambian |
| `products/` | Ninguno — CRUD B2B sin cambios |
| `images/` | Ninguno |
| `saasadmin/` | Ninguno |
| `db/models.py` | `Tenant` tiene 2 campos nuevos; `ConsumerEvent` añadido |

---

## Próximo paso

**PASO 2 — Auto-Onboarding de Tenants**

Nuevo endpoint público:
```
POST /api/v1/auth/register-tenant
```

Responsabilidades:
1. Validar que el email no exista
2. Generar `slug` único desde `business_name`
3. Crear el tenant con `subscription_status = 'trial'`
4. Crear el primer usuario con `role = 'business_admin'`
5. Retornar JWT de sesión inmediato

Archivos a modificar: `auth/service.py`, `auth/routes.py`, `auth/models.py`, `db/models.py`.
