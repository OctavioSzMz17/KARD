# KARD — Guía de Arquitectura General
**Documento de orientación** — cómo funciona el proyecto y dónde vive cada cosa.  
Última actualización: 2026-07-05

> Este documento responde: *"¿dónde hago X?"*, *"¿dónde vive Y?"*, *"¿cómo se comunica Z?"*.  
> Para el contrato de la API del cliente ver [`API_Frontend_Cliente.md`](API_Frontend_Cliente.md).

---

## 1. Qué es KARD

Plataforma **SaaS marketplace B2B2C**. Tres actores:

- **Plataforma (KARD)** — administra todo, cobra suscripciones.
- **Negocios (tenants)** — restaurantes y ópticas. Pagan suscripción, administran su catálogo desde un dashboard.
- **Clientes (consumers)** — entran a la plataforma, descubren tiendas, compran.

```
Negocio paga suscripción → crea su tienda → administra productos
                                                     ↓
Cliente entra a KARD → explora categorías → elige tienda → compra
```

---

## 2. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.11 |
| Framework web | FastAPI |
| ORM | SQLAlchemy 2.0 |
| Validación | Pydantic v2 |
| Base de datos | PostgreSQL (Neon en la nube) |
| Cache / carrito / sesiones | Redis 7 |
| Auth | JWT (python-jose) + bcrypt (passlib) |
| Emails | Resend |
| Pagos | Stripe *(skeleton, sin conectar)* |
| Contenedores | Docker + Docker Compose |

**Frontend:** separado, en otro repo/chat. Este proyecto es **solo backend** (API REST JSON).

---

## 3. Patrón de diseño — capas por feature

Cada módulo sigue el mismo patrón de **3 capas**. Regla de oro: la lógica de negocio vive en el `service`, nunca en las rutas ni en el repositorio.

```
routes.py       →  Solo HTTP: parsea request, llama al service, devuelve respuesta.
                   Sin lógica de negocio. "Thin controllers".
     ↓
service.py      →  TODA la lógica: reglas, validaciones, permisos, transacciones.
                   Aquí es donde se modifica el comportamiento.
     ↓
repository.py   →  Solo queries a la DB. Sin reglas, sin commits, sin decisiones.
     ↓
models.py       →  Schemas Pydantic (request/response) del módulo.
```

> **¿Dónde cambio la lógica de algo?** → siempre en el `service.py` del módulo.
> **¿Dónde agrego un endpoint?** → `routes.py` del módulo + registrarlo en `main.py`.
> **¿Dónde cambio qué campos recibe/devuelve?** → `models.py` del módulo.
> **¿Dónde cambio una query?** → `repository.py` del módulo.

---

## 4. Estructura del proyecto

```
Kard/
├── main.py                  ← ENTRYPOINT: registra routers + exception handlers
├── docker-compose.yml       ← define backend + db + redis
├── requirements.txt         ← dependencias Python
├── kard_database.sql        ← schema fuente de verdad (tablas, enums, constraints)
├── .env                     ← configuración (NO se sube a git)
│
├── core/                    ← INFRAESTRUCTURA COMPARTIDA
│   ├── config.py            ← lee el .env (settings singleton)
│   ├── dependencies.py      ← auth: get_current_user, get_current_consumer
│   ├── security.py          ← hash de passwords, crear/decodificar JWT
│   ├── redis_client.py      ← conexión Redis + cache helpers
│   └── exceptions.py        ← excepciones de dominio (todas juntas)
│
├── db/
│   ├── database.py          ← engine SQLAlchemy + get_db (sesión por request)
│   └── models.py            ← modelos ORM (todas las tablas)
│
├── utils/helpers.py         ← generate_id() y utilidades generales
│
│  ─── MÓDULOS B2B (negocio) ───
├── auth/                    ← login, registro de tenant, crear usuarios
├── products/               ← CRUD de productos + inventario (dueño del negocio)
├── images/                 ← galería de imágenes de productos
├── saasadmin/              ← administración de la plataforma (super admin)
├── businessadmin/          ← administración del negocio
├── billing/                ← suscripciones Stripe (SKELETON, sin lógica)
│
│  ─── MÓDULOS B2C (cliente) ───
├── storefront/             ← tienda pública por slug (GET /s/{slug})
├── explore/                ← descubrimiento: categorías + listado de tiendas
├── consumers/              ← identidad global del cliente (register/login/me)
├── cart/                   ← carrito (vive en Redis)
├── orders/                 ← pedidos (viven en PostgreSQL)
│
│  ─── TRANSVERSALES ───
├── notifications/          ← emails transaccionales (Resend)
├── audit_logging/          ← logs de auditoría de operaciones
│
├── migrations/             ← SQL incremental que se corre a mano en Neon
├── seeders/                ← scripts para poblar datos de demo
├── tests/                  ← pytest
└── docs/reportes/          ← reportes de cada paso + esta guía
```

---

## 5. Los dos mundos: B2B vs B2C

Esta es la distinción más importante del proyecto. Son **dos tipos de identidad completamente separados**, con tokens que NO son intercambiables.

| | B2B (negocio) | B2C (cliente) |
|---|---|---|
| Tabla | `users` | `consumers` |
| Login | `POST /api/v1/auth/login` | `POST /api/v1/consumers/login` |
| JWT lleva | `user_id, tenant_id, role, business_type` | `consumer_id, type: "consumer"` |
| Dependency | `get_current_user` | `get_current_consumer` |
| `tenant_id` viene de | el JWT (verificado) | el **slug** en la URL |
| Alcance | un solo negocio | toda la plataforma |

**¿Cómo se protege un endpoint?** Con la dependency correcta:
```python
# Endpoint de negocio:
def mi_ruta(user: CurrentUser = Depends(get_current_user)): ...

# Endpoint de cliente:
def mi_ruta(consumer: CurrentConsumer = Depends(get_current_consumer)): ...
```
El discriminador `type: "consumer"` en el JWT impide que un token B2B toque rutas B2C y viceversa. Vive en [`core/dependencies.py`](../../core/dependencies.py).

---

## 6. Multi-tenancy — aislamiento entre negocios

Regla crítica: un negocio nunca ve datos de otro.

- **`tenant_id` NUNCA se confía del request** (body/query/path). Siempre del JWT verificado (B2B) o del slug (B2C).
- Toda query a datos de negocio filtra por `tenant_id`.
- Un recurso de otro tenant devuelve **404** — indistinguible de "no existe" (no se filtra información de existencia).

**¿Dónde vive esto?** En cada `repository.py` — todas las queries llevan `.filter(Model.tenant_id == tenant_id)`.

---

## 7. Comunicación y formatos

- **Protocolo:** REST sobre HTTP. Todo JSON (request y response), UTF-8.
- **Auth header:** `Authorization: Bearer <token>` en rutas protegidas.
- **Respuesta exitosa:** el objeto directo, sin sobre.
- **Respuesta de error:** `{ "success": false, "error": "mensaje" }`
- **Errores de validación (Pydantic):** `{ "detail": [...] }` con status 422.
- **Paginación:** `{ items, total, page, page_size }`, params `?page=1&page_size=20`.
- **Fechas:** ISO 8601 con timezone.

Los **exception handlers** que traducen excepción de dominio → status HTTP viven todos en [`main.py`](../../main.py). Ahí se define qué código devuelve cada error.

---

## 8. Dónde vive cada dato

| Dato | Dónde vive | Por qué |
|---|---|---|
| Negocios, usuarios, productos, pedidos | PostgreSQL (Neon) | Permanente, auditable |
| Carrito de compras | **Redis** (`cart:{consumer_id}:{slug}`) | Temporal, TTL 7 días |
| Cache del catálogo público | Redis (`storefront:catalog:...`) | TTL 5 min, se invalida en cada cambio |
| Bloqueo por intentos de login | Redis | Efímero |
| Blacklist de tokens (logout) | Redis | Efímero |
| Eventos de comportamiento del cliente | PostgreSQL (`consumer_events`) | Para IA en fase futura |

**Regla Redis:** el cache degrada con gracia (si Redis cae, se lee de la DB). El carrito NO — el carrito **es** Redis, si cae devuelve 503.

---

## 9. Flujo típico de una request

Ejemplo: cliente agrega producto al carrito.

```
POST /api/v1/cart/buena-vista/items   (con Bearer token de consumer)
        ↓
[main.py]           router dirige a cart/routes.py
        ↓
[cart/routes.py]    valida body con Pydantic, resuelve el consumer via
                    get_current_consumer (core/dependencies.py)
        ↓
[cart/service.py]   LÓGICA: valida que el producto exista, sea 'final',
                    del tenant correcto; escribe en Redis
        ↓
[Redis]             cart:{consumer_id}:buena-vista = {...}
        ↓
respuesta JSON      CartResponse con items, total, item_count
```

---

## 10. Configuración — el .env

Todo lo configurable vive en `.env` (leído por [`core/config.py`](../../core/config.py)). **Nunca se hardcodea nada.**

| Variable | Para qué |
|---|---|
| `DATABASE_URL` | Conexión a Postgres (Neon activo / local Docker comentado) |
| `REDIS_URL` | Conexión a Redis (`redis://redis:6379` dentro de Docker) |
| `JWT_SECRET_KEY` | Firma de tokens |
| `RESEND_API_KEY`, `EMAIL_FROM` | Envío de emails |
| `CORS_ORIGINS` | Orígenes permitidos del frontend |
| `APP_URL` | Base URL para links en emails |
| `PORT_BACKEND/DB/REDIS` | Mapeo de puertos de Docker al host |

---

## 11. Cómo se levanta

```bash
docker compose up -d              # levanta backend + db + redis
docker compose up -d --build backend   # tras cambiar código Python
docker logs kard-backend-1 -f     # ver logs
```

- Backend: `http://localhost:8000`
- Swagger (docs interactivas): `http://localhost:8000/docs`
- Contenedores: `kard-backend-1`, `kard-db-1`, `kard-redis-1`

---

## 12. Base de datos — cómo se cambia el schema

1. La fuente de verdad del schema completo es [`kard_database.sql`](../../kard_database.sql).
2. Los cambios incrementales van en `migrations/NNN_descripcion.sql`.
3. Las migraciones se corren **a mano en el SQL Editor de Neon** (no hay Alembic).
4. El modelo ORM correspondiente se actualiza en [`db/models.py`](../../db/models.py).

> **¿Agrego una tabla?** → migración SQL + modelo en `db/models.py` + correr en Neon.

---

## 13. Estado del proyecto (MVP Fase 1)

| # | Módulo | Estado |
|---|---|---|
| 1 | Migración DB (slug, consumer_events) | ✅ |
| 2 | Registro self-service de tenant | ✅ |
| 3 | Storefront público | ✅ |
| 4 | Cache Redis del catálogo | ✅ |
| 5 | Identidad global de consumer | ✅ |
| 6 | Billing (Stripe) | 🟡 skeleton |
| 7 | Notificaciones email (Resend) | ✅ |
| 8 | Explore / descubrimiento | ✅ |
| 9 | Carrito + Pedidos (POS) | ✅ *(pendiente migración 003 en Neon)* |

**Pendiente conectar:** Stripe real (billing) para pasar pedidos de `pending` → `paid`.
