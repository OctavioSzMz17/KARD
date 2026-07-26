# KARD — Product Backlog y Sprint Backlog
**Entregable:** Primer Avance · Punto 5 (Metodología Scrum)  
**Equipo:** Octavio Sánchez Mendoza · Brayan Adonai Díaz Santiago  
**Sprint 1:** 02-jul-2026 (jue) → 09-jul-2026 (jue) · 8 días

---

## 1. Product Backlog (priorizado)

Ordenado por prioridad y valor de negocio. Todo el backlog entró al Sprint 1 (primer avance = MVP funcional completo).

| # | HU | Descripción | SP | Prioridad | Estado |
|:--:|:--:|---|:--:|:--:|:--:|
| 1 | HU-01 | Registro self-service de negocio | 5 | 🔴 | ✅ Done |
| 2 | HU-02 | Login de negocio (JWT) | 3 | 🔴 | ✅ Done |
| 3 | HU-03 | CRUD de productos | 8 | 🔴 | ✅ Done |
| 4 | HU-04 | Gestión de inventario | 5 | 🔴 | ✅ Done |
| 5 | HU-06 | Storefront público por slug | 5 | 🔴 | ✅ Done |
| 6 | HU-07 | Explorar tiendas por categoría | 3 | 🔴 | ✅ Done |
| 7 | HU-08 | Registro/login de cliente | 3 | 🔴 | ✅ Done |
| 8 | HU-09 | Carrito de compras | 5 | 🔴 | ✅ Done |
| 9 | HU-10 | Confirmar pedido (checkout) | 8 | 🔴 | ✅ Done |
| 10 | HU-05 | Imágenes de productos | 3 | 🟡 | ✅ Done |
| 11 | HU-11 | Notificaciones por email | 3 | 🟡 | ✅ Done |
| 12 | HU-12 | Caché Redis del catálogo | 3 | 🟡 | ✅ Done |
| 13 | HU-13 | Base de pagos (Stripe skeleton) | 2 | 🟢 | ✅ Done |
| | | **TOTAL** | **56** | | |

---

## 2. Sprint Backlog — plan diario

Capacidad objetivo: ~7 SP/día. Fin de semana (Jul 4-5) planeado más ligero.

| Fecha | Día | HU trabajadas | SP del día | Responsable(s) |
|---|---|---|:--:|---|
| 02-jul | Jue | HU-01 Registro negocio · HU-02 Login negocio | 8 | Octavio |
| 03-jul | Vie | HU-03 CRUD de productos | 8 | Octavio |
| 04-jul | Sáb | HU-12 Caché Redis · HU-13 Stripe skeleton | 5 | Octavio |
| 05-jul | Dom | HU-05 Imágenes de productos | 3 | Brayan |
| 06-jul | Lun | HU-04 Inventario · HU-06 Storefront público | 10 | Octavio |
| 07-jul | Mar | HU-07 Explorar · HU-08 Login cliente · HU-11 Emails | 9 | Brayan / Octavio |
| 08-jul | Mié | HU-09 Carrito de compras | 5 | Octavio |
| 09-jul | Jue | HU-10 Confirmar pedido (checkout) | 8 | Octavio |
| | | **TOTAL** | **56** | |

---

## 3. Definición de "Terminado" (Definition of Done)

Una HU se considera **Done** cuando:
- ✅ El código está implementado siguiendo el patrón routes → service → repository.
- ✅ El endpoint responde el formato JSON acordado y maneja errores.
- ✅ Se probó manualmente (Postman / Swagger) contra el backend en Docker.
- ✅ Está documentada en su reporte correspondiente (`docs/reportes/`).
- ✅ El código está commiteado y subido a GitHub.

---

## 4. Ceremonias Scrum realizadas

| Ceremonia | Cuándo | Notas |
|---|---|---|
| Sprint Planning | 02-jul | Se priorizó el backlog y se estimaron las HU en puntos. |
| Daily (async) | Diario | Actualización de tarjetas en Trello y avance del burndown. |
| Sprint Review | 09-jul | Demo del MVP funcional (registro → catálogo → carrito → pedido). |
| Retrospectiva | 09-jul | Ver sección 5. |

---

## 5. Retrospectiva del Sprint 1

**Qué salió bien 👍**
- El patrón por capas (routes/service/repository) permitió agregar módulos sin romper lo existente.
- Separar carrito (Redis) de pedidos (PostgreSQL) simplificó el checkout.

**Qué mejorar 🔧**
- Algunas migraciones se corrieron a mano en Neon; automatizarlas a futuro.
- El fin de semana se subestimó la carga; equilibrar mejor la capacidad.

**Acciones para el próximo sprint ✅**
- Conectar Stripe real para pasar pedidos de `pending` → `paid`.
- Agregar vista de "Mis pedidos" en el frontend.
