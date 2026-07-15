# KARD — Requisitos, Casos de Uso e Historias de Usuario Valoradas
**Entregable:** Primer Avance · Punto 1 (CU/HU y requisitos)  
**Equipo:** Octavio Sánchez Mendoza · Brayan Adonai Díaz Santiago  
**Sprint:** 02-jul-2026 → 09-jul-2026

---

## 1. Descripción del proyecto

**KARD** es una plataforma SaaS tipo *marketplace* B2B2C. Los negocios (restaurantes y ópticas) pagan una suscripción, crean su tienda y administran su catálogo. Los clientes entran a la plataforma, descubren tiendas por categoría, arman su carrito y realizan pedidos.

**Actores:**
- **Negocio (business_admin)** — dueño/empleado que administra su tienda.
- **Cliente (consumer)** — identidad global que compra en cualquier tienda pública.
- **Administrador de plataforma (saas_admin)** — gestiona los negocios de KARD.

---

## 2. Requisitos funcionales (RF)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-01 | El negocio puede registrarse de forma self-service (crea tenant + su primer admin) | Alta |
| RF-02 | El negocio puede iniciar sesión con email y contraseña (JWT) | Alta |
| RF-03 | El negocio puede crear, editar, listar y eliminar (soft-delete) productos | Alta |
| RF-04 | El negocio puede administrar inventario (vender/reabastecer) con historial | Alta |
| RF-05 | El negocio puede subir imágenes a sus productos | Media |
| RF-06 | El cliente puede ver la tienda pública de un negocio por su URL (slug) | Alta |
| RF-07 | El cliente puede explorar tiendas por categoría y búsqueda | Alta |
| RF-08 | El cliente puede registrarse e iniciar sesión (identidad global) | Alta |
| RF-09 | El cliente puede agregar productos a un carrito por tienda | Alta |
| RF-10 | El cliente puede confirmar un pedido (checkout) que descuenta inventario | Alta |
| RF-11 | El sistema envía correos transaccionales (bienvenida, etc.) | Media |
| RF-12 | El catálogo público se sirve con caché para acelerar la respuesta | Media |
| RF-13 | El sistema deja lista la integración de pagos por suscripción (Stripe) | Baja |

## 3. Requisitos no funcionales (RNF)

| ID | Requisito |
|---|---|
| RNF-01 | Aislamiento multi-tenant: un negocio nunca accede a datos de otro (respuesta 404) |
| RNF-02 | El `tenant_id` nunca se confía del request; se deriva del JWT o del slug |
| RNF-03 | Contraseñas cifradas con bcrypt; autenticación por JWT |
| RNF-04 | Toda la configuración vive en variables de entorno (`.env`), nada hardcodeado |
| RNF-05 | La API responde JSON con formato de error uniforme `{success, error}` |
| RNF-06 | Degradación con gracia: si Redis del caché cae, se responde desde la BD |
| RNF-07 | El checkout es atómico (bloqueo de fila) para evitar sobreventa |

---

## 4. Casos de uso

```
        ┌─────────────────────── KARD ───────────────────────┐
        │                                                      │
 Negocio│  (CU-01) Registrar negocio                           │
   ○────┼─▶ (CU-02) Iniciar sesión                             │
  /|\   │  (CU-03) Gestionar productos                         │
  / \   │  (CU-04) Gestionar inventario                        │
        │  (CU-05) Subir imágenes                              │
        │                                                      │
 Cliente│  (CU-06) Explorar tiendas                            │
   ○────┼─▶ (CU-07) Ver catálogo de tienda                     │
  /|\   │  (CU-08) Registrarse / iniciar sesión                │
  / \   │  (CU-09) Administrar carrito                         │
        │  (CU-10) Confirmar pedido                            │
        │                                                      │
SaaS    │  (CU-11) Administrar negocios de la plataforma       │
 admin ○┼─▶                                                    │
        └──────────────────────────────────────────────────────┘
```

### Detalle de casos de uso clave

**CU-10 — Confirmar pedido**
- **Actor:** Cliente
- **Precondición:** Cliente autenticado, carrito con al menos un producto.
- **Flujo principal:**
  1. El cliente solicita confirmar el pedido de una tienda.
  2. El sistema lee el carrito (Redis) y bloquea las filas de producto.
  3. Verifica stock suficiente para cada ítem.
  4. Descuenta inventario y registra la transacción.
  5. Crea el pedido y sus líneas en la base de datos.
  6. Vacía el carrito y devuelve el pedido con estado `pending`.
- **Flujo alterno:** Si algún producto no tiene stock → se cancela toda la operación (rollback) y el carrito queda intacto.

**CU-06 — Explorar tiendas**
- **Actor:** Cliente
- **Flujo:** El cliente consulta las categorías disponibles → filtra por categoría o busca por nombre → obtiene la lista de tiendas públicas → selecciona una y navega a su catálogo.

---

## 5. Historias de Usuario valoradas (HU)

Estimación con **Story Points** en escala Fibonacci (1, 2, 3, 5, 8).  
Prioridad: 🔴 Alta · 🟡 Media · 🟢 Baja.

| ID | Como… | Quiero… | Para… | SP | Prioridad | Responsable |
|---|---|---|---|:--:|:--:|---|
| HU-01 | negocio | registrarme solo en la plataforma | tener mi tienda sin intervención manual | 5 | 🔴 | Octavio |
| HU-02 | negocio | iniciar sesión de forma segura | administrar mi tienda | 3 | 🔴 | Octavio |
| HU-03 | negocio | gestionar mis productos (CRUD) | mantener mi catálogo actualizado | 8 | 🔴 | Octavio |
| HU-04 | negocio | controlar mi inventario | saber cuánto stock tengo | 5 | 🔴 | Octavio |
| HU-05 | negocio | subir imágenes a mis productos | mostrarlos mejor al cliente | 3 | 🟡 | Brayan |
| HU-06 | cliente | ver la tienda pública de un negocio | conocer sus productos | 5 | 🔴 | Octavio |
| HU-07 | cliente | explorar tiendas por categoría | encontrar el negocio que busco | 3 | 🔴 | Brayan |
| HU-08 | cliente | registrarme e iniciar sesión | comprar en cualquier tienda | 3 | 🔴 | Brayan |
| HU-09 | cliente | agregar productos a un carrito | preparar mi compra | 5 | 🔴 | Octavio |
| HU-10 | cliente | confirmar mi pedido | completar la compra | 8 | 🔴 | Octavio |
| HU-11 | sistema | enviar correos de bienvenida | mejorar la experiencia | 3 | 🟡 | Octavio |
| HU-12 | plataforma | cachear el catálogo público | responder más rápido | 3 | 🟡 | Octavio |
| HU-13 | plataforma | dejar lista la base de pagos | poder cobrar suscripciones después | 2 | 🟢 | Octavio |
| | | | **TOTAL** | **56** | | |

### Ejemplo de criterios de aceptación

**HU-10 — Confirmar pedido (8 SP)**
- ✅ Dado un carrito con productos, al confirmar se crea un pedido con estado `pending`.
- ✅ El inventario de cada producto se descuenta por la cantidad pedida.
- ✅ Si un producto no tiene stock suficiente, no se crea ningún pedido y el carrito se conserva.
- ✅ El carrito se vacía únicamente si el pedido se creó correctamente.
- ✅ Un cliente solo puede ver sus propios pedidos.

---

## 6. Resumen de esfuerzo

- **Total de puntos del sprint:** 56 SP
- **Octavio Sánchez Mendoza:** 42 SP (HU 01-04, 06, 09-13)
- **Brayan Adonai Díaz Santiago:** 14 SP (HU 05, 07, 08)
