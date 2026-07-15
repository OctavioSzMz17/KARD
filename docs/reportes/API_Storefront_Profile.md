# KARD — API Reference: Contenido de la Tienda (eslogan, descripción, contacto)
**Para:** el chat del frontend (KARD-Client / dashboard del negocio)
**Estado:** ✅ Implementado y verificado en vivo
**Base URL:** `http://localhost:8000` (dev)
**Complementa a:** [`API_Branding.md`](API_Branding.md) (logo, banner, tema)

> **Implementado tal cual el spec.** Los nombres de campo son exactamente los que el front ya cablea. No cambió nada del contrato.

---

## 0. Resumen

`branding` (visual) y `profile` (editorial) son **dos endpoints independientes** que viven en `Tenant.config` (JSONB, sin migración).

| Contexto | Endpoint | Auth |
|---|---|---|
| El dueño lee su contenido | `GET /api/v1/settings/profile` | 🔒 Negocio |
| El dueño edita su contenido | `PUT /api/v1/settings/profile` | 🔒 Negocio |
| El cliente ve la tienda | `GET /s/{slug}` (extendido) | Público |
| El cliente explora | `GET /api/v1/explore` (extendido) | Público |

---

## 1. Contrato de campos (tipos exactos)

```typescript
type HourEntry = {
  days:  string;   // "Lunes a Viernes"
  hours: string;   // "9:00 – 18:00" | "Cerrado"
};

type Social = {
  facebook:  string | null;
  instagram: string | null;
  tiktok:    string | null;
  x:         string | null;
  youtube:   string | null;
};

type Contact = {
  address:  string | null;
  phone:    string | null;
  email:    string | null;
  whatsapp: string | null;
  maps_url: string | null;
  hours:    HourEntry[];   // [] si no hay
  social:   Social;        // siempre presente (con nulls)
};

type Profile = {
  tagline:     string | null;
  description: string | null;
  contact:     Contact;    // siempre presente (con nulls)
};
```

**Garantías de forma (iguales a `theme` en branding):**
- `contact` y `contact.social` **siempre vienen** en la respuesta, nunca ausentes.
- `hours` **siempre es un array** (vacío si no hay).
- Todo lo demás puede ser `null` → el front aplica sus fallbacks.
- Se puede leer `store.contact.phone` sin miedo a `undefined`.

---

## 2. B2B — El dueño edita el contenido

Header requerido:
```
Authorization: Bearer <token_de_negocio>
```
El tenant sale del token — nunca por parámetro (aislamiento multi-tenant).

---

### 2.1 `GET /api/v1/settings/profile`

**Response `200`:**
```json
{
  "tagline": "Cocina mexicana de siempre",
  "description": "Somos un restaurante familiar con más de 20 años preparando recetas de casa.",
  "contact": {
    "address": "Av. Reforma 123, Col. Centro, CDMX",
    "phone": "+52 55 1234 5678",
    "email": "hola@tiarosa.com",
    "whatsapp": "+52 55 1234 5678",
    "maps_url": "https://maps.google.com/?q=19.4326,-99.1332",
    "hours": [
      { "days": "Lunes a Viernes", "hours": "9:00 – 18:00" },
      { "days": "Domingo", "hours": "Cerrado" }
    ],
    "social": {
      "facebook": "https://facebook.com/tiarosa",
      "instagram": null, "tiktok": null, "x": null, "youtube": null
    }
  }
}
```

**Sin configurar (verificado en vivo):**
```json
{
  "tagline": null,
  "description": null,
  "contact": {
    "address": null, "phone": null, "email": null,
    "whatsapp": null, "maps_url": null,
    "hours": [],
    "social": { "facebook": null, "instagram": null, "tiktok": null, "x": null, "youtube": null }
  }
}
```

---

### 2.2 `PUT /api/v1/settings/profile`

**Reemplazo total** (misma semántica que `PUT /settings/branding`): lo omitido se guarda como `null` / `[]`.

**Request:** mismo shape que el `GET`.
**Response `200`:** el objeto ya guardado (idéntico al `GET`).

**Ejemplos válidos:**
```json
// Solo eslogan (borra description y contact)
{ "tagline": "Cocina mexicana de siempre" }

// Limpiar todo
{}
```

> ⚠️ **Reemplazo total:** mandar `{ "tagline": "..." }` **borra** description y contact. Si el front quiere conservar lo demás, debe enviar el objeto completo con los valores actuales (mismo criterio que branding).

**Validaciones:**

| Campo | Regla | Si falla |
|---|---|---|
| `tagline` | máx 120 chars, o `null` | `422` |
| `description` | máx 2000 chars, o `null` | `422` |
| `contact.address` | máx 300 chars, o `null` | `422` |
| `contact.phone` | máx 30 chars, o `null` | `422` |
| `contact.email` | email válido, o `null` | `422` |
| `contact.whatsapp` | máx 40 chars, o `null` | `422` |
| `contact.maps_url` | URL `http(s)`, máx 2048, o `null` | `422` |
| `contact.hours` | array máx **10**; `days` máx 60, `hours` máx 60 | `422` |
| `contact.social.*` | URL `http(s)`, máx 2048, o `null` | `422` |

**Errores:**
| Status | Cuándo |
|---|---|
| `401` | Sin token / inválido / expirado |
| `422` | Validación (largo, email, URL) — cuerpo `{ "detail": [...] }` |

---

## 3. Público — Servir el contenido al cliente

### 3.1 `GET /s/{slug}` — shape completo (branding + profile)

Sin token. **Response `200` real, verificado en vivo:**
```json
{
  "slug": "nombre-del-restaurante",
  "business_name": "Tia rosa",
  "business_type": "restaurant",

  "logo_url": null,
  "banner_url": null,
  "theme": { "primary": "#e11d48", "accent": "#881337" },

  "tagline": "Cocina mexicana de siempre",
  "description": "Somos un restaurante familiar con más de 20 años.",
  "contact": {
    "address": "Av. Reforma 123, CDMX",
    "phone": "+52 55 1234 5678",
    "email": "hola@tiarosa.com",
    "whatsapp": "+52 55 1234 5678",
    "maps_url": "https://maps.google.com/?q=19.4326,-99.1332",
    "hours": [
      { "days": "Lunes a Viernes", "hours": "9:00 - 18:00" },
      { "days": "Domingo", "hours": "Cerrado" }
    ],
    "social": {
      "facebook": "https://facebook.com/tiarosa",
      "instagram": null, "tiktok": null, "x": null, "youtube": null
    }
  }
}
```

**Errores:** `404` si el slug no existe o la tienda no es pública.

> **Sobre la caché:** `GET /s/{slug}` **no tiene caché** — los cambios de perfil/marca se ven al instante. (La caché solo aplica a `GET /s/{slug}/products`, y se invalida sola cuando el negocio edita.)

### 3.2 `GET /api/v1/explore` — con `tagline`

**Response `200` real:**
```json
{
  "items": [
    {
      "slug": "nombre-del-restaurante",
      "business_name": "Tia rosa",
      "business_type": "restaurant",
      "logo_url": "https://cdn.tunegocio.com/logo.png",
      "tagline": "Cocina mexicana de siempre"
    },
    {
      "slug": "buena-vista",
      "business_name": "Buena vista",
      "business_type": "optician",
      "logo_url": null,
      "tagline": null
    }
  ],
  "total": 2, "page": 1, "page_size": 20
}
```
> Solo `logo_url` + `tagline` (la card no necesita description ni contact). Si `tagline` es `null`, el front usa su texto genérico por tipo de negocio.

---

## 4. ⚠️ Importante para el front — los dos PUTs comparten columna

`branding` y `profile` viven en la **misma columna JSONB** (`Tenant.config`). El backend ya protege esto con un **bloqueo de fila** (`SELECT FOR UPDATE`), así que **puedes disparar ambos PUTs en paralelo sin miedo**: se serializan y ninguno pisa al otro.

**Verificado:** guardar `branding` y luego `profile` deja ambas claves intactas.

Aun así, la recomendación es que cada pantalla guarde **solo su endpoint**:
- Pantalla "Diseño" → `PUT /settings/branding`
- Pantalla "Información" → `PUT /settings/profile`

---

## 5. Flujo típico en el front

```
DASHBOARD DEL NEGOCIO
─────────────────────
1. Abrir "Información de la tienda":
   GET /api/v1/settings/profile     (token)
   → precarga eslogan, descripción, contacto, horarios, redes

2. Guardar:
   PUT /api/v1/settings/profile     (token, objeto completo)
   → 200 con lo guardado · validación falla → 422

STOREFRONT (landing del cliente)
────────────────────────────────
3. GET /s/{slug}                    (público)
   → hero: business_name + tagline
   → sección "Sobre nosotros": description
   → footer: contact.{address,phone,email,whatsapp,maps_url,hours,social}
   → tema: theme.primary / theme.accent → CSS vars

4. Catálogo (página aparte):
   GET /s/{slug}/products
```

---

## 6. Snippet de referencia

```typescript
// Guardar perfil (dashboard) — enviar SIEMPRE el objeto completo
await fetch(`${API}/api/v1/settings/profile`, {
  method: "PUT",
  headers: {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${businessToken}`,
  },
  body: JSON.stringify({
    tagline: tagline || null,
    description: description || null,
    contact: {
      address: address || null,
      phone: phone || null,
      email: email || null,
      whatsapp: whatsapp || null,
      maps_url: mapsUrl || null,
      hours: hours,            // [] si no hay
      social: {
        facebook: fb || null, instagram: ig || null,
        tiktok: tt || null, x: xx || null, youtube: yt || null,
      },
    },
  }),
});

// Landing pública
const store = await fetch(`${API}/s/${slug}`).then(r => r.json());
// hero
const heroTitle = store.business_name;
const heroSubtitle = store.tagline ?? "";          // sin eslogan → solo el nombre
// footer — contact SIEMPRE existe, sus campos pueden ser null
const hasContact = !!(store.contact.address || store.contact.phone || store.contact.email);
store.contact.hours.forEach(h => renderHour(h.days, h.hours));   // siempre array
```

---

## 7. Decisiones de diseño (para contexto)

**a) `profile` separado de `branding`** — Aprobado tal cual el spec. Contratos chicos, no se toca lo que ya funciona.

**b) `hours` como texto libre** — Aprobado. Flexible para horarios reales ("Lun–Vie 9–18, Dom cerrado"). **Contra conocido:** no permite calcular "¿está abierto ahora?". Si se quiere a futuro, habría que migrar a horas estructuradas.

**c) `contact.phone` vs la columna `tenants.phone`** — Existen los dos, **a propósito**, porque son de audiencias distintas:

| | `tenants.phone` (columna) | `profile.contact.phone` (JSONB) |
|---|---|---|
| Quién lo edita | El admin de la plataforma (onboarding) | **El negocio** |
| Para qué | Contacto administrativo / de cuenta | **Público** — footer de la tienda |
| Visible al cliente | ❌ No | ✅ Sí |
| Largo | 20 chars | 30 chars |

> **Para el front:** usa siempre `contact.phone` (el de `profile`). La columna `tenants.phone` es interna y no se expone en ningún endpoint público.

**d) Concurrencia** — Se agregó bloqueo de fila en ambos PUTs para evitar que uno pise al otro (ver §4). Invisible para el front.

---

## 8. Notas para no desincronizar

- Nombres **fijos**: `tagline`, `description`, `contact.{address,phone,email,whatsapp,maps_url,hours,social}`, `social.{facebook,instagram,tiktok,x,youtube}`.
- `contact` y `contact.social` siempre presentes; `hours` siempre array. Mismo criterio que `theme`.
- Sin subida de archivos — solo texto y URLs.
- Si el back necesitara cambiar un nombre, se avisa antes.

---

## 9. Datos de demo listos para probar

El tenant **`nombre-del-restaurante`** (Tia rosa) ya tiene contenido cargado para que puedan construir la landing:
- `tagline`, `description`, `contact` completo (dirección, teléfono, email, whatsapp, maps, 2 horarios, Facebook)
- `theme`: `#e11d48` / `#881337`
- `logo_url` / `banner_url` en `null` → prueben los fallbacks

El tenant **`buena-vista`** (óptica) está **vacío** a propósito → sirve para probar el caso "todo null".
