# KARD — API Reference: Personalización de Marca (Branding)
**Para:** el chat del frontend (KARD-Client / dashboard del negocio)
**Estado:** ✅ Implementado y verificado en vivo
**Base URL:** `http://localhost:8000` (dev)
**Formato:** JSON en request y response · UTF-8

> Contrato de campos **fijo** — el backend respeta exactamente los nombres que el front ya cablea: `logo_url`, `banner_url`, `theme.primary`, `theme.accent`. No cambian.

---

## 0. Resumen

Cada negocio (tenant) puede definir **logo, banner y 2 colores de tema**. Se guarda en `Tenant.config["branding"]` (JSONB, sin migración). Se sirve en 3 lugares:

| Contexto | Endpoint | Auth |
|---|---|---|
| El dueño lee su marca | `GET /api/v1/settings/branding` | 🔒 Negocio |
| El dueño edita su marca | `PUT /api/v1/settings/branding` | 🔒 Negocio |
| El cliente ve la tienda | `GET /s/{slug}` (extendido) | Público |
| El cliente explora | `GET /api/v1/explore` (extendido) | Público |

**Alcance MVP:** logo y banner son **URLs** (strings). No hay subida de archivos — el negocio pega un link.

---

## 1. Contrato de campos (tipos exactos)

```typescript
type Theme = {
  primary: string | null;   // hex "#RRGGBB"
  accent:  string | null;   // hex "#RRGGBB"
};

type Branding = {
  logo_url:   string | null;   // URL http(s)
  banner_url: string | null;   // URL http(s)
  theme:      Theme | null;    // el backend siempre devuelve el objeto theme (con nulls), nunca lo omite
};
```

- **Todos los campos son opcionales/nulos.** Un tenant sin configurar devuelve todo en `null`. El front aplica sus defaults de KARD.
- El objeto `theme` **siempre viene presente** (nunca falta la clave); sus dos colores pueden ser `null`.

---

## 2. B2B — El dueño edita su marca

Requieren **JWT de negocio** en el header:
```
Authorization: Bearer <token_de_negocio>
```
El tenant sale del token (`tenant_id`) — **nunca** se manda por parámetro (aislamiento multi-tenant).

---

### 2.1 `GET /api/v1/settings/branding`

Devuelve la marca actual del negocio autenticado.

**Response `200`:**
```json
{
  "logo_url": "https://cdn.tunegocio.com/logo.png",
  "banner_url": "https://cdn.tunegocio.com/banner.jpg",
  "theme": {
    "primary": "#5b6ef5",
    "accent": "#1e3a8a"
  }
}
```

**Sin configurar (tenant nuevo):**
```json
{
  "logo_url": null,
  "banner_url": null,
  "theme": { "primary": null, "accent": null }
}
```

---

### 2.2 `PUT /api/v1/settings/branding`

**Reemplaza** la marca completa. Los campos que omitas se guardan como `null` (reemplazo total, no parcial — más simple de razonar).

**Request:**
```json
{
  "logo_url": "https://cdn.tunegocio.com/logo.png",
  "banner_url": "https://cdn.tunegocio.com/banner.jpg",
  "theme": {
    "primary": "#5b6ef5",
    "accent": "#1e3a8a"
  }
}
```

**Response `200`:** el mismo objeto ya guardado (idéntico al `GET`).

**Reglas de validación:**

| Campo | Regla | Si falla |
|---|---|---|
| `logo_url` | URL que inicie con `http://` o `https://`, o `null`. Máx 2048 chars. | `422` |
| `banner_url` | Igual que `logo_url`. | `422` |
| `theme.primary` | Hex `#RRGGBB` (regex `^#([0-9a-fA-F]{6})$`), o `null`. | `422` |
| `theme.accent` | Igual que `primary`. | `422` |

**Ejemplos de payload válidos:**
```json
// Solo logo, sin tema
{ "logo_url": "https://x.com/l.png" }

// Solo colores
{ "theme": { "primary": "#ff5722", "accent": "#263238" } }

// Limpiar todo (volver a defaults de KARD)
{}
```

> ⚠️ Como es reemplazo total, mandar `{ "logo_url": "..." }` **borra** el banner y el tema anteriores. Si el front quiere conservar lo demás, debe mandar el objeto completo con los valores actuales.

**Comportamiento interno (informativo):**
- Se hace **merge** sobre `config` — solo se toca la clave `branding`, el resto de `config` se preserva.
- Se **invalida la caché** del storefront de ese slug, para que el cliente vea el cambio sin esperar el TTL.

---

### 2.3 Errores B2B

| Status | Cuándo | Cuerpo |
|---|---|---|
| `401` | Sin token / token inválido / expirado | `{ "success": false, "error": "..." }` |
| `422` | Hex mal formado o URL inválida | `{ "detail": [ { "loc": [...], "msg": "..." } ] }` |

**Ejemplo `422` (hex inválido):**
```json
{
  "detail": [
    {
      "loc": ["body", "theme", "primary"],
      "msg": "Value error, El color debe ser hexadecimal en formato #RRGGBB",
      "type": "value_error"
    }
  ]
}
```
> Los `422` de validación NO usan el sobre `{success:false}` — llegan como `{ "detail": [...] }` (estándar de FastAPI/Pydantic).

---

## 3. Público — Servir la marca al cliente

### 3.1 `GET /s/{slug}` (extendido)

Sin token. Ahora incluye la marca junto a la info del negocio.

**Response `200`:**
```json
{
  "slug": "buena-vista",
  "business_name": "Buena vista",
  "business_type": "optician",
  "logo_url": "https://cdn.tunegocio.com/logo.png",
  "banner_url": "https://cdn.tunegocio.com/banner.jpg",
  "theme": {
    "primary": "#5b6ef5",
    "accent": "#1e3a8a"
  }
}
```

- Si el negocio no configuró marca → `logo_url`, `banner_url` en `null` y `theme` con ambos en `null`.
- El front pinta el tema con sus CSS variables (`--sf-primary` / `--sf-accent`) y aplica defaults cuando llega `null`.

**Errores:** `404` si el slug no existe o la tienda no es pública (igual que antes).

---

### 3.2 `GET /api/v1/explore` (extendido)

Cada item ahora trae `logo_url` para mostrar el logo del negocio en la card (o el placeholder si es `null`).

**Response `200`:**
```json
{
  "items": [
    {
      "slug": "buena-vista",
      "business_name": "Buena vista",
      "business_type": "optician",
      "logo_url": "https://cdn.tunegocio.com/logo.png"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

> Solo `explore` trae `logo_url` (no el banner ni el tema — la card no los necesita).

---

## 4. Flujo típico en el front

```
DASHBOARD DEL NEGOCIO
─────────────────────
1. Al abrir "Personalización":
   GET /api/v1/settings/branding   (con token)
   → precarga el formulario con logo/banner/colores actuales

2. El dueño edita y guarda:
   PUT /api/v1/settings/branding   (con token, objeto completo)
   → 200 con lo guardado · si hay hex/URL inválido → 422

STOREFRONT DEL CLIENTE
──────────────────────
3. Al entrar a una tienda:
   GET /s/{slug}                   (público)
   → aplica theme.primary/accent a las CSS vars y pinta logo+banner

4. En el marketplace:
   GET /api/v1/explore             (público)
   → muestra logo_url en cada card
```

---

## 5. Snippet de referencia (fetch)

```typescript
// Guardar branding (dashboard)
await fetch(`${API}/api/v1/settings/branding`, {
  method: "PUT",
  headers: {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${businessToken}`,
  },
  body: JSON.stringify({
    logo_url: logoUrl || null,
    banner_url: bannerUrl || null,
    theme: { primary: primaryHex || null, accent: accentHex || null },
  }),
});

// Leer branding público (storefront)
const store = await fetch(`${API}/s/${slug}`).then(r => r.json());
document.documentElement.style.setProperty("--sf-primary", store.theme?.primary ?? DEFAULT_PRIMARY);
document.documentElement.style.setProperty("--sf-accent",  store.theme?.accent  ?? DEFAULT_ACCENT);
```

---

## 6. Notas para no desincronizar

- Nombres de campo **fijos**: `logo_url`, `banner_url`, `theme.primary`, `theme.accent`. Si el back tuviera que cambiar alguno, avisar al front.
- `theme` siempre viene como objeto (con nulls), nunca ausente — el front puede leer `store.theme.primary` sin miedo a `undefined`, aunque conviene el `?.` por robustez.
- No hay upload de archivos: el front envía **URLs**. Si en el futuro se agrega upload, este contrato no cambia (solo se sumaría un endpoint de subida que devuelva la URL).
