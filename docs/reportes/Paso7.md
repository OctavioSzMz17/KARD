# Reporte de Implementación — PASO 7: Email Notifications (Resend)
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 7 de 7 — Notificaciones Transaccionales  
**Fecha:** 2026-06-18  
**Estado:** ✅ Listo para producción (pendiente API key de Resend)

---

## Resumen

El módulo `notifications/` implementa emails transaccionales a través de **Resend**. Toda la configuración viene del `.env` — cero valores hardcodeados. Mientras `RESEND_API_KEY` esté vacío (o Resend no esté disponible), el sistema funciona igual que antes: las operaciones se completan, las notificaciones se saltan con un log de debug.

---

## Activación (cuando tengas la cuenta)

1. Crea cuenta en [resend.com](https://resend.com) → gratuito
2. Genera un API key en **API Keys → Create API Key**
3. Agrega al `.env`:

```
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxx
EMAIL_FROM=noreply@tudominio.com
EMAIL_FROM_NAME=KARD
APP_URL=https://tuapp.com
```

4. Para desarrollo sin dominio propio, Resend te permite usar `onboarding@resend.dev` como sender — solo cambia `EMAIL_FROM=onboarding@resend.dev`.

No hay que reiniciar Docker ni reconstruir la imagen — solo agregar las variables al `.env` y reiniciar el backend.

---

## Módulo `notifications/`

```
notifications/
├── __init__.py
├── email_client.py   ← wrapper Resend con degradación graceful
├── templates.py      ← 5 plantillas HTML inline-styled
└── service.py        ← NotificationService con métodos estáticos
```

### `email_client.send_email()`

```
RESEND_API_KEY vacío → log debug, return False (silencioso)
RESEND_API_KEY presente → resend.Emails.send(...)
  ├─ Éxito → log info, return True
  └─ Excepción → log warning, return False (nunca propaga)
```

El import de `resend` es lazy (dentro del `try`) — si el paquete no está instalado, la app arranca igual y el log lo advierte.

---

## Templates disponibles

| Template | Destinatario | Cuándo se dispara |
|---|---|---|
| `tenant_welcome` | `tenant.email` (business_admin) | `POST /auth/register-tenant` exitoso |
| `consumer_welcome` | `consumer.email` | `POST /api/v1/consumers/register` exitoso |
| `low_stock_alert` | `tenant.email` | Llamada manual desde productos (ver abajo) |
| `payment_confirmed` | `tenant.email` | Webhook `checkout.session.completed` (Billing) |
| `payment_failed` | `tenant.email` | Webhook `invoice.payment_failed` (Billing) |

---

## Eventos ya conectados

### `POST /auth/register-tenant`
En `auth/service.py`, después del `self.db.commit()`:
```python
NotificationService.tenant_welcome(
    email=tenant.email,
    business_name=tenant.business_name,
    slug=tenant.slug,
)
```

### `POST /api/v1/consumers/register`
En `consumers/service.py`, después del `self.db.commit()`:
```python
NotificationService.consumer_welcome(email=consumer.email, name=consumer.name)
```

---

## Low Stock Alert — pendiente de trigger

La plantilla existe, el método `NotificationService.low_stock_alert()` está listo. El trigger debe conectarse en `products/service.py` cuando se hace `adjust_stock()` o `sell_product()`. Ejemplo:

```python
# En products/service.py → adjust_stock() o sell_product()
LOW_STOCK_THRESHOLD = 5

if product.stock_quantity <= LOW_STOCK_THRESHOLD:
    tenant = self.db.query(Tenant).filter(Tenant.id == product.tenant_id).first()
    NotificationService.low_stock_alert(
        email=tenant.email,
        product_name=product.name,
        current_stock=product.stock_quantity,
        business_name=tenant.business_name,
    )
```

El umbral `LOW_STOCK_THRESHOLD` debería venir de `.env` en el futuro (por ahora puede ser constante).

---

## Payment emails — pendiente de Billing

`payment_confirmed` y `payment_failed` se conectan en `billing/service.py` dentro de `handle_webhook()`:

```python
# checkout.session.completed
NotificationService.payment_confirmed(
    email=tenant.email,
    business_name=tenant.business_name,
    plan=event.data.object.metadata["plan"],
)

# invoice.payment_failed
NotificationService.payment_failed(
    email=tenant.email,
    business_name=tenant.business_name,
    plan=tenant.plan,
)
```

---

## Variables de entorno

| Variable | Obligatoria | Default | Descripción |
|---|---|---|---|
| `RESEND_API_KEY` | No* | `""` | API key de Resend. Vacío = emails desactivados |
| `EMAIL_FROM` | Sí (si key presente) | `""` | Dirección del remitente |
| `EMAIL_FROM_NAME` | No | `"KARD"` | Nombre visible en el inbox |
| `APP_URL` | No | `"http://localhost:3000"` | Base URL para links en los emails |

*Vacío es válido — el sistema funciona sin enviar emails.

---

## Resumen de archivos modificados

| Archivo | Cambio |
|---|---|
| `requirements.txt` | `+ resend` |
| `core/config.py` | `+ RESEND_API_KEY, EMAIL_FROM, EMAIL_FROM_NAME, APP_URL` |
| `.env.example` | Sección Resend documentada |
| `auth/service.py` | Import `NotificationService` + llamada post-commit en `register_tenant` |
| `consumers/service.py` | Import `NotificationService` + llamada post-commit en `register` |
| `notifications/__init__.py` | Nuevo (vacío) |
| `notifications/email_client.py` | Nuevo — wrapper Resend |
| `notifications/templates.py` | Nuevo — 5 templates HTML |
| `notifications/service.py` | Nuevo — `NotificationService` |
