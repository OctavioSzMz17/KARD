# KARD — Estructura del Tablero Trello
**Entregable:** Primer Avance · Punto 5 (Metodología — Trello)  
**Equipo:** Octavio Sánchez Mendoza · Brayan Adonai Díaz Santiago

> Este documento describe cómo armar el tablero de Trello para reflejar el Sprint 1.
> Cópialo tarjeta por tarjeta al crear el tablero real.

---

## 1. Listas (columnas) del tablero

```
┌────────────┬────────────┬──────────────┬────────────┬──────────┐
│  BACKLOG   │  SPRINT 1  │ EN PROGRESO  │ EN REVISIÓN│   DONE   │
│ (Product   │ (To Do)    │ (Doing)      │ (Review)   │          │
│  Backlog)  │            │              │            │          │
└────────────┴────────────┴──────────────┴────────────┴──────────┘
```

Al cierre del Sprint 1, **todas las tarjetas están en DONE**.

---

## 2. Etiquetas (labels) sugeridas

| Color | Etiqueta | Uso |
|---|---|---|
| 🔴 Rojo | Prioridad Alta | HU críticas del MVP |
| 🟡 Amarillo | Prioridad Media | HU complementarias |
| 🟢 Verde | Prioridad Baja | HU opcionales |
| 🔵 Azul | Backend | Trabajo de API |
| 🟣 Morado | Base de datos | Migraciones / modelos |
| 🟠 Naranja | B2C Cliente | Módulos del cliente |

---

## 3. Tarjetas (una por Historia de Usuario)

Cada tarjeta lleva: **título**, **puntos (SP)**, **responsable**, **etiquetas**, **checklist de criterios de aceptación** y **fecha de vencimiento**.

| Tarjeta | SP | Responsable | Etiquetas | Vence | Lista |
|---|:--:|---|---|---|:--:|
| HU-01 Registro self-service de negocio | 5 | Octavio | Alta, Backend | 02-jul | Done |
| HU-02 Login de negocio (JWT) | 3 | Octavio | Alta, Backend | 02-jul | Done |
| HU-03 CRUD de productos | 8 | Octavio | Alta, Backend | 03-jul | Done |
| HU-12 Caché Redis del catálogo | 3 | Octavio | Media, Backend | 04-jul | Done |
| HU-13 Base de pagos (Stripe skeleton) | 2 | Octavio | Baja, Backend | 04-jul | Done |
| HU-05 Imágenes de productos | 3 | Brayan | Media, Backend | 05-jul | Done |
| HU-04 Gestión de inventario | 5 | Octavio | Alta, Base de datos | 06-jul | Done |
| HU-06 Storefront público por slug | 5 | Octavio | Alta, B2C Cliente | 06-jul | Done |
| HU-07 Explorar tiendas por categoría | 3 | Brayan | Alta, B2C Cliente | 07-jul | Done |
| HU-08 Registro/login de cliente | 3 | Brayan | Alta, B2C Cliente | 07-jul | Done |
| HU-11 Notificaciones por email | 3 | Octavio | Media, Backend | 07-jul | Done |
| HU-09 Carrito de compras | 5 | Octavio | Alta, B2C Cliente | 08-jul | Done |
| HU-10 Confirmar pedido (checkout) | 8 | Octavio | Alta, B2C Cliente | 09-jul | Done |

---

## 4. Ejemplo de una tarjeta completa

```
╔══════════════════════════════════════════════════╗
║  HU-10 · Confirmar pedido (checkout)               ║
║  ───────────────────────────────────────────────  ║
║  Puntos: 8 SP        Responsable: Octavio          ║
║  Etiquetas: 🔴 Alta  🟠 B2C Cliente                 ║
║  Vence: 09-jul-2026                                 ║
║                                                     ║
║  Descripción:                                       ║
║  Como cliente quiero confirmar mi pedido para       ║
║  completar la compra.                               ║
║                                                     ║
║  Checklist (criterios de aceptación):               ║
║   ☑ Crea pedido con estado 'pending'                ║
║   ☑ Descuenta inventario por cantidad               ║
║   ☑ Si falta stock, rollback y carrito intacto      ║
║   ☑ Vacía carrito solo si el pedido se creó         ║
║   ☑ Cliente solo ve sus propios pedidos             ║
╚══════════════════════════════════════════════════╝
```

---

## 5. Recursos del equipo (checklist de metodología)

| Recurso | Estado | Enlace / nota |
|---|---|---|
| Repositorio GitHub | ✅ Creado | github.com/OctavioSzMz17/KARD |
| Tablero Trello | ⬜ Por crear | Usar esta estructura |
| Drive del equipo | ⬜ Por crear | Carpeta compartida con los documentos de esta entrega |
| Gráfica de quemado | ✅ Creada | Ver `03_Burndown.md` |
| HU valoradas | ✅ Creadas | Ver `01_Requisitos_HU_Valoradas.md` |
