# Reporte de Implementación — PASO 6: Billing / Stripe (Skeleton)
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 6 de 7 — Integración de Pagos  
**Fecha:** 2026-06-18  
**Estado:** 🟡 Skeleton — endpoints registrados, lógica pendiente

---

## Resumen

El módulo `billing/` existe y está registrado en la aplicación. Los tres endpoints responden `501 Not Implemented` hasta que se conecte Stripe. La excepción `PlanLimitExceededError` está disponible para que los servicios B2B puedan hacer enforcement de límites de plan en cuanto las columnas billing existan en la DB.

---

## Endpoints (todos retornan 501 por ahora)

**Prefijo:** `/api/v1/billing`

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/api/v1/billing/checkout` | B2B JWT | Inicia sesión de pago Stripe |
| POST | `/api/v1/billing/webhook` | Firma Stripe | Recibe eventos de Stripe |
| GET | `/api/v1/billing/plan` | B2B JWT | Estado actual del plan |

El webhook está marcado `include_in_schema=False` — no aparece en Swagger porque no debe documentarse como endpoint público.

---

## Checklist de implementación

### 1. Dependencia y variables de entorno

```bash
pip install stripe
```

Añadir al `.env`:
```
STRIPE_SECRET_KEY=sk_live_...        # o sk_test_... para desarrollo
STRIPE_WEBHOOK_SECRET=whsec_...      # obtenido en el dashboard de Stripe
```

Añadir a `core/config.py`:
```python
STRIPE_SECRET_KEY: str = ""
STRIPE_WEBHOOK_SECRET: str = ""
```

---

### 2. Migration 003 — Columnas billing en `tenants`

```sql
-- migrations/003_billing_columns.sql
ALTER TABLE tenants
  ADD COLUMN stripe_customer_id     VARCHAR(100),
  ADD COLUMN stripe_subscription_id VARCHAR(100),
  ADD COLUMN subscription_status    VARCHAR(30) NOT NULL DEFAULT 'trial',
  ADD COLUMN plan                   VARCHAR(30) NOT NULL DEFAULT 'trial',
  ADD COLUMN trial_ends_at          TIMESTAMPTZ;

COMMENT ON COLUMN tenants.subscription_status IS 'trial | active | past_due | canceled | paused';
COMMENT ON COLUMN tenants.plan                IS 'trial | starter | pro | enterprise';
```

Añadir al modelo `Tenant` en `db/models.py`:
```python
stripe_customer_id     = Column(String(100), nullable=True)
stripe_subscription_id = Column(String(100), nullable=True)
subscription_status    = Column(String(30),  nullable=False, default="trial")
plan                   = Column(String(30),  nullable=False, default="trial")
trial_ends_at          = Column(DateTime(timezone=True), nullable=True)
```

---

### 3. Flujo completo

```
1. Frontend llama POST /api/v1/billing/checkout { plan: "starter" }
        ↓
2. BillingService.create_checkout_session()
   → stripe.checkout.Session.create(
       customer=tenant.stripe_customer_id,   # crea si no existe
       mode="subscription",
       line_items=[{ price: PRICE_IDS[plan] }],
       success_url="...",
       cancel_url="...",
       metadata={"tenant_id": tenant.id},   # para recuperarlo en webhook
     )
   → retorna session.url
        ↓
3. Usuario paga en Stripe
        ↓
4. Stripe llama POST /api/v1/billing/webhook
   → stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
   → según event.type:
       "checkout.session.completed"   → subscription_status=active, plan=X
       "customer.subscription.deleted"→ subscription_status=canceled
       "invoice.payment_failed"       → subscription_status=past_due
```

---

### 4. Idempotencia — Evitar Cargos Dobles

Este es el punto más crítico de cualquier integración de pagos. Stripe puede reenviar el mismo webhook hasta 3 veces si el endpoint no responde `200`. Sin idempotencia, el mismo evento podría procesarse varias veces.

#### Estrategia recomendada: `stripe_event_id` en Redis

```python
async def handle_webhook(self, payload: bytes, stripe_signature: str) -> None:
    event = stripe.Webhook.construct_event(
        payload, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
    )

    # Idempotencia: si ya procesamos este evento, ignorar
    redis_client = get_redis()
    if redis_client:
        idempotency_key = f"stripe:event:{event.id}"
        if redis_client.exists(idempotency_key):
            logger.info("Duplicate Stripe event ignored: %s", event.id)
            return
        # Marcarlo antes de procesar (TTL 24h cubre todos los reintentos de Stripe)
        redis_client.set(idempotency_key, "1", ex=86_400)

    # Procesar el evento
    if event.type == "checkout.session.completed":
        self._handle_checkout_completed(event.data.object)
    elif event.type == "customer.subscription.deleted":
        self._handle_subscription_canceled(event.data.object)
    elif event.type == "invoice.payment_failed":
        self._handle_payment_failed(event.data.object)
```

#### Por qué Redis y no DB

| Opción | Ventaja | Problema |
|---|---|---|
| `processed_events` en DB | Persistente, fácil de auditar | Una escritura a DB antes de cada pago, FK overhead |
| Redis con TTL | Sub-milisegundo, sin overhead | Si Redis cae, el chequeo se saltea (pero el cliente ya tiene `_unavailable`) |
| Stripe `idempotency_key` en el request | Stripe maneja duplicados del lado del request | No cubre duplicados del webhook — son independientes |

**Elección:** Redis con TTL 24 horas. Los webhooks de Stripe se reintentan máximo 72 horas, pero con TTL de 24h cubrimos todas las redes de reintentos normales. Si Redis está caído, el código tiene fallthrough a la DB (la operación de UPDATE es idempotente si los valores son los mismos — `SET subscription_status='active'` dos veces no hace daño).

#### Idempotencia en `create_checkout_session`

```python
# Usar stripe_customer_id como ancla — si ya existe, reutilizarlo
tenant = self.tenants.get_by_id(tenant_id)
if not tenant.stripe_customer_id:
    customer = stripe.Customer.create(
        email=tenant.email,
        metadata={"tenant_id": tenant.id}
    )
    tenant.stripe_customer_id = customer.id
    self.tenants.save(tenant)
    self.db.commit()

# Crear la sesión con idempotency_key de Stripe
session = stripe.checkout.Session.create(
    customer=tenant.stripe_customer_id,
    ...,
    idempotency_key=f"checkout:{tenant.id}:{plan}:{int(time.time() // 3600)}",
    # La clave incluye la hora truncada a horas — si el usuario hace doble clic
    # en la misma hora, Stripe devuelve la misma sesión en lugar de crear otra.
)
```

---

### 5. Enforcement de plan en B2B (cuando esté activo)

En `products/service.py`, antes de `create_product`:

```python
def _check_plan_limit(self, tenant_id: str) -> None:
    tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant.plan == "starter":
        count = self.products.count_active(tenant_id)
        if count >= 100:
            raise PlanLimitExceededError(
                "Plan Starter: límite de 100 productos activos alcanzado. "
                "Actualiza tu plan para agregar más."
            )
```

`PlanLimitExceededError` → HTTP 402 Payment Required (ya registrado en `main.py`).

---

## Archivos del Skeleton

```
billing/
├── __init__.py
├── models.py    ← CheckoutRequest, CheckoutResponse, PlanInfo
├── service.py   ← BillingService (3 métodos con NotImplementedError)
└── routes.py    ← POST /checkout, POST /webhook, GET /plan → 501
```

| Archivo adicional | Cambio |
|---|---|
| `core/exceptions.py` | `PlanLimitExceededError` |
| `main.py` | Import + handler 402 + `include_router` |

---

## Próximo Paso

**PASO 7 — Emails Transaccionales (`notifications/`)**

Módulo de notificaciones por email para:
- Bienvenida al registrar tenant
- Bienvenida al registrar consumer  
- Stock bajo detectado
- Confirmación de pago (cuando Stripe esté activo)
