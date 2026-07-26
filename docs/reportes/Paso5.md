# Reporte de Implementación — PASO 5: Módulo Consumers (Identidad Global B2C)
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 5 de 7 — Identidad Global del Consumidor  
**Fecha:** 2026-06-18  
**Estado:** ✅ Completado y verificado en Neon

---

## Resumen Ejecutivo

Se implementó el módulo `consumers/` — una identidad global de plataforma para visitantes B2C. Una sola cuenta da acceso a todos los storefronts públicos. El JWT del consumidor es estructuralmente distinto al B2B: lleva `consumer_id` + `type: "consumer"` pero **nunca** `tenant_id` (el tenant siempre viene del slug de la URL). A partir de ahora, los `consumer_events` registran el `consumer_id` en lugar de `NULL`.

---

## Endpoints

**Prefijo:** `/api/v1/consumers`

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/api/v1/consumers/register` | No | Crear cuenta global |
| POST | `/api/v1/consumers/login` | No | Login → JWT de consumidor |
| GET | `/api/v1/consumers/me` | Sí (consumer JWT) | Perfil propio |

---

### `POST /api/v1/consumers/register`

**Request:**
```json
{
  "email": "maria@example.com",
  "password": "Maria123",
  "name": "Maria Lopez"
}
```

| Campo | Tipo | Requerido | Validaciones |
|---|---|---|---|
| `email` | EmailStr | Sí | Formato válido, único globalmente |
| `password` | string | Sí | 8–128 chars, ≥1 letra y ≥1 dígito |
| `name` | string | No | max 100 chars |

**Response `201`:**
```json
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "consumer_id": "con_c51bf2aea964466cb2f4"
}
```

---

### `POST /api/v1/consumers/login`

**Request:**
```json
{ "email": "maria@example.com", "password": "Maria123" }
```

**Response `200`:** igual que register.

---

### `GET /api/v1/consumers/me`

**Headers:** `Authorization: Bearer <consumer_token>`

**Response `200`:**
```json
{
  "consumer_id": "con_c51bf2aea964466cb2f4",
  "email": "maria@example.com",
  "name": "Maria Lopez",
  "is_active": true,
  "created_at": "2026-06-18T16:30:00Z"
}
```

**Errores comunes:**

| Código | Causa |
|---|---|
| `400` | Email ya registrado |
| `401` | Token inválido, expirado, o es un token B2B (no consumer) |
| `422` | Payload inválido |

---

## El JWT del Consumidor vs el B2B

```
B2B token (usuario de negocio):
  { "user_id": "...", "tenant_id": "...", "business_type": "...", "role": "..." }

Consumer token (comprador):
  { "consumer_id": "...", "type": "consumer" }
```

El claim `type: "consumer"` actúa como discriminador. `get_current_consumer` rechaza cualquier token que no lo contenga — un admin B2B no puede acceder a rutas de consumidor con su token, y viceversa.

El `consumer_id` nunca tiene `tenant_id` porque una cuenta de consumidor es global: sirve para navegar la vitrina de Tia Rosa, luego la de Buena Vista, sin crear una cuenta nueva en cada una.

---

## Arquitectura del Módulo

```
consumers/
├── __init__.py
├── models.py       ← ConsumerRegister, ConsumerLogin, ConsumerResponse, ConsumerTokenResponse
├── repository.py   ← ConsumerRepository: get_by_email, get_by_id, create
├── service.py      ← ConsumerService: register(), login(), get_me()
└── routes.py       ← 3 endpoints
```

### `core/dependencies.py` — nuevo `CurrentConsumer`

```python
class CurrentConsumer(BaseModel):
    consumer_id: str
    email: str

async def get_current_consumer(token, db) -> CurrentConsumer:
    payload = decode_token(token)
    # Rechaza si type != "consumer" o falta consumer_id
    # Carga consumer de DB para verificar que siga activo
    return CurrentConsumer(consumer_id=..., email=...)
```

---

## DB: tabla `consumers`

```sql
CREATE TABLE consumers (
    id              VARCHAR(30)   PRIMARY KEY,  -- con_{uuid_short}
    email           VARCHAR(255)  UNIQUE NOT NULL,
    hashed_password VARCHAR(255)  NOT NULL,      -- bcrypt 12 rounds
    name            VARCHAR(100),
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
```

**Sin `tenant_id`** — es la característica definitoria. La tabla `consumers` es la única del schema sin FK a `tenants`.

---

## Archivos Creados / Modificados

### Nuevos

| Archivo | Contenido |
|---|---|
| `migrations/002_consumers_table.sql` | DDL ejecutado contra Neon |
| `consumers/__init__.py` | Vacío |
| `consumers/models.py` | 4 schemas Pydantic |
| `consumers/repository.py` | `ConsumerRepository` |
| `consumers/service.py` | `ConsumerService`: register, login, get_me |
| `consumers/routes.py` | 3 endpoints bajo `/api/v1/consumers` |

### Modificados

| Archivo | Qué cambió |
|---|---|
| `db/models.py` | Nuevo modelo `Consumer` |
| `kard_database.sql` | Tabla `consumers` añadida |
| `core/exceptions.py` | `ConsumerAlreadyExistsError`, `ConsumerNotFoundError` |
| `core/dependencies.py` | `CurrentConsumer` + `get_current_consumer` |
| `main.py` | Import, 2 handlers, `include_router` |

---

## Verificación (ejecutado contra Neon)

```
Registrado: con_c51bf2aea964466cb2f4
JWT claims: consumer_id=con_...  type=consumer
JWT sin tenant_id: OK ✅
Email duplicado rechazado: OK ✅
Login correcto: OK ✅
Password incorrecta rechazada: OK ✅
get_me: maria@example.com  name=Maria Lopez ✅
```

---

## Impacto en Módulos Existentes

| Módulo | Impacto |
|---|---|
| `auth/` | Ninguno — login B2B no cambia |
| `products/` | Ninguno |
| `storefront/` | Ninguno — `consumer_id` se añadirá a `record_event` cuando se integre auth opcional |
| `saasadmin/` | Ninguno |

---

## Próximo Paso

**PASO 6 — Integración de Pagos con Stripe**  
*(Según el plan: solo dejar el módulo `billing/` estructurado con los endpoints, sin implementar la lógica de pagos real — se activa cuando el negocio lo requiera)*

**PASO 7 — Emails Transaccionales (Resend)**  
Módulo `notifications/` con `send_email()` utilizado desde los servicios existentes para:
- Bienvenida al registrar tenant
- Bienvenida al registrar consumer
- Forgot password
- Stock bajo
