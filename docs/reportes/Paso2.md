# Reporte de Implementación — PASO 2: Auto-Onboarding de Tenants
**Proyecto:** KARD  
**Fase:** MVP Fase 1 — Plataforma B2B2C  
**Paso:** 2 de 7 — Self-Service Registration  
**Fecha:** 2026-06-18  
**Estado:** ✅ Completado y verificado en producción (Neon)

---

## Resumen Ejecutivo

Se implementó el endpoint público `POST /api/v1/auth/register-tenant` que permite a cualquier negocio crear su cuenta en KARD sin intervención manual del SaaS admin. El proceso es atómico: tenant + primer usuario admin + slug único se crean en un solo commit. El nuevo admin recibe un JWT inmediatamente y puede acceder al panel sin un login separado.

---

## Nuevo Endpoint

```
POST /api/v1/auth/register-tenant
```

**Público — no requiere autenticación**

### Request

```json
{
  "business_name": "Papelería Don Jorge",
  "business_type": "restaurant",
  "email": "jorge@papeleria.com",
  "password": "MiPass123"
}
```

| Campo | Tipo | Requerido | Validaciones |
|---|---|---|---|
| `business_name` | string | Sí | 2–255 chars, no puede ser blank |
| `business_type` | enum | Sí | `restaurant` \| `optician` |
| `email` | EmailStr | Sí | Formato de email válido |
| `password` | string | Sí | 8–128 chars, mínimo 1 letra y 1 número |

### Response `201 Created`

```json
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "user_id": "u_4a2f1b9c8d3e",
  "tenant_id": "t_7c3e9f1a2b4d",
  "role": "business_admin",
  "business_type": "restaurant",
  "slug": "papeleria-don-jorge"
}
```

El campo `slug` es exclusivo de esta respuesta — los otros endpoints de auth no lo incluyen. El frontend puede usarlo inmediatamente para mostrar la URL de la vitrina: `app.com/s/papeleria-don-jorge`.

### Errores

| Código | Causa |
|---|---|
| `400` | Email ya registrado (en users O en tenants) |
| `422` | Payload inválido (campo faltante, password débil, email malformado) |

---

## Lógica de Generación de Slug

El slug se genera automáticamente desde `business_name` siguiendo estas transformaciones:

```
"Papelería Don Jorge"
    → NFKD normalization   → "Papeleria Don Jorge" (á → a)
    → ASCII encode/decode  → "Papeleria Don Jorge"
    → lowercase            → "papeleria don jorge"
    → non-alphanumeric→-   → "papeleria-don-jorge"
    → strip leading/trail  → "papeleria-don-jorge"
    → truncate 55 chars    → "papeleria-don-jorge"
```

**Colisiones:** si el slug ya existe, se añade sufijo numérico:
```
"papeleria-don-jorge"    ← intento 1
"papeleria-don-jorge-2"  ← intento 2 (si el primero existe)
"papeleria-don-jorge-3"  ← intento 3...
```

**Casos probados en Neon:**

| Input | Slug generado |
|---|---|
| `"Papeleria Don Jorge"` | `papeleria-don-jorge` |
| `"Tia Rosa"` | `tia-rosa` |
| `"Optica Vision Clara"` | `optica-vision-clara` |
| `"La Taqueria El Guero"` | `la-taqueria-el-guero` |
| `"La Taqueria El Guero"` (colisión) | `la-taqueria-el-guero-2` |

---

## Flujo Interno

```
POST /api/v1/auth/register-tenant
        │
        ▼
AuthService.register_tenant()
        │
        ├─ 1. email en users?     → 400 Email already registered
        ├─ 2. email en tenants?   → 400 Email already registered
        ├─ 3. _generate_unique_slug(business_name)
        │         └─ _slugify() + check DB + sufijo numérico si colisión
        │
        ├─ 4. Crear Tenant {slug, is_public=False, is_active=True}
        ├─ 5. Crear User  {role=business_admin, email=mismo del tenant}
        │
        ├─ 6. Commit atómico (flush → commit)
        │         └─ IntegrityError → rollback → 400
        │
        ├─ 7. log_operation(REGISTER_TENANT)
        ├─ 8. create_token({user_id, tenant_id, business_type, role})
        │
        └─ 9. TenantRegistrationResponse {+ slug}
```

---

## Archivos Modificados

### `core/exceptions.py` ← nueva excepción

```python
class TenantAlreadyExistsError(Exception):
    """Raised when self-service registration is attempted with an email already in use by a tenant."""
```

### `auth/models.py` ← dos nuevos schemas

```python
class TenantRegistrationRequest(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=255)
    business_type: BusinessType  # "restaurant" | "optician"
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

class TenantRegistrationResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    tenant_id: str
    role: str
    business_type: str
    slug: str              # ← exclusivo de este endpoint
```

### `auth/repository.py` ← dos nuevos métodos en TenantRepository

```python
def get_by_slug(self, slug: str) -> Optional[Tenant]:
    return self.db.query(Tenant).filter(Tenant.slug == slug).first()

def save(self, tenant: Tenant) -> Tenant:
    self.db.add(tenant)
    self.db.flush()
    return tenant
```

### `auth/service.py` ← tres nuevos métodos en AuthService

| Método | Responsabilidad |
|---|---|
| `register_tenant(payload)` | Orquesta la creación atómica de tenant + usuario |
| `_generate_unique_slug(name)` | Itera hasta encontrar slug disponible |
| `_slugify(text)` | Transforma texto libre a URL-safe slug |

### `auth/routes.py` ← nueva ruta

```python
POST /api/v1/auth/register-tenant   → register_tenant()   [sin auth]
```

### `main.py` ← nuevo exception handler

```python
@app.exception_handler(TenantAlreadyExistsError)
async def tenant_already_exists_exception_handler(request, exc):
    return JSONResponse(status_code=400, content=_error("Email is already registered"))
```

---

## Impacto en módulos existentes

| Módulo | Impacto |
|---|---|
| `auth/` | Endpoint nuevo, sin cambios en login ni create_user |
| `products/` | Ninguno |
| `images/` | Ninguno |
| `saasadmin/` | Ninguno |
| Tenants en producción | Ninguno — sus slugs siguen en NULL |

---

## Estado de Tenants en Producción

| id | business_name | slug | is_public |
|---|---|---|---|
| `t_1b9d1e408d35` | KARD | `NULL` | `false` |
| `t_9a40982067c4` | Tia rosa | `NULL` | `false` |
| `t_e107cda08be3` | Buena vista | `NULL` | `false` |

Los tenants existentes no tienen slug asignado — eso ocurrirá cuando el PASO 2 onboarde tenants nuevos vía el endpoint, o cuando el admin asigne el slug manualmente (PASO 2 cubre solo nuevos registros; los existentes se migran en una tarea separada).

---

## Próximo Paso

**PASO 3 — Módulo `storefront/` (Vitrina Pública B2C)**

Nuevas rutas públicas sin auth:
```
GET /s/{slug}                  → Info pública del negocio
GET /s/{slug}/products         → Catálogo paginado (solo product_type=final)
GET /s/{slug}/products/{id}    → Detalle de un producto
```

Solo tenants con `is_public=TRUE` responden en estas rutas. El resto devuelve 404 indistinguible de "no existe" (Rules.md §7.5 — misma filosofía que el aislamiento B2B).
